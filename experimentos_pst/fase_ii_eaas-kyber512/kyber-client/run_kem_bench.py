#!/usr/bin/env python3
from __future__ import annotations

import base64
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

HEX_RE = re.compile(r"^[0-9a-fA-F]+$")

SIZE_BITS = 2048
EXPECTED_BYTES = SIZE_BITS // 8
EXPECTED_HEX_CHARS = SIZE_BITS // 4  # 512


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_entropy_from_eaas(api_url: str, timeout_s: float = 5.0):
    """
    Retorna (seed_hex, seed_bytes, sha256_hex, key_id).
    Espera JSON com slice_hex e/ou slice_b64.
    """
    p = subprocess.run(
        ["curl", "-sS", "-X", "POST", api_url],
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )
    stdout = (p.stdout or "").strip()
    stderr = (p.stderr or "").strip()

    if p.returncode != 0:
        raise RuntimeError(f"EaaS curl exit={p.returncode} stderr={stderr[:200]!r}")
    if not stdout:
        raise RuntimeError("EaaS EMPTY_STDOUT")

    try:
        obj = json.loads(stdout)
    except Exception as e:
        raise RuntimeError(f"EaaS BAD_JSON len={len(stdout)} err={e}")

    # auditoria: size_bits e key_id
    sb = obj.get("size_bits", None)
    if sb is not None and int(sb) != SIZE_BITS:
        raise RuntimeError(f"EaaS BAD_SIZE_BITS got={sb} expected={SIZE_BITS}")

    key_id = obj.get("key_id", None)

    # preferir slice_hex
    slice_hex = obj.get("slice_hex")
    if isinstance(slice_hex, str) and slice_hex:
        hex_str = slice_hex.strip()
        if not HEX_RE.match(hex_str) or len(hex_str) != EXPECTED_HEX_CHARS:
            raise RuntimeError(f"EaaS BAD_HEX_FIELD chars={len(hex_str)} expected={EXPECTED_HEX_CHARS}")

        b = bytes.fromhex(hex_str)
        if len(b) != EXPECTED_BYTES:
            raise RuntimeError(f"EaaS BAD_LEN_HEX bytes={len(b)} expected={EXPECTED_BYTES}")

        h = hashlib.sha256(b).hexdigest()
        return hex_str.lower(), b, h, key_id

    # fallback: slice_b64
    slice_b64 = obj.get("slice_b64")
    if isinstance(slice_b64, str) and slice_b64:
        try:
            b = base64.b64decode(slice_b64.strip(), validate=True)
        except Exception as e:
            raise RuntimeError(f"EaaS BAD_B64_FIELD err={e}")
        if len(b) != EXPECTED_BYTES:
            raise RuntimeError(f"EaaS BAD_LEN_B64 bytes={len(b)} expected={EXPECTED_BYTES}")
        h = hashlib.sha256(b).hexdigest()
        return b.hex(), b, h, key_id

    raise RuntimeError("EaaS MISSING_FIELDS: slice_hex/slice_b64")


def fetch_entropy_from_direct(driver_path: str, port: str, baud: str, timeout: str, margin_samples: str):
    """
    Chama o qrng_direct.py como caixa-preta e retorna (seed_hex, seed_bytes, sha256_hex).
    Assumimos que o driver imprime HEX puro quando --format hex.
    """
    cmd = [
        "python3", driver_path,
        "--port", port,
        "--baud", str(baud),
        "--timeout", str(timeout),
        "--size-bits", str(SIZE_BITS),
        "--format", "hex",
        "--margin-samples", str(margin_samples),
        "--er", "5",  # ER fixo em 5 (alinhado ao artigo)
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=60.0, check=False)
    so = (p.stdout or "").strip()
    se = (p.stderr or "").strip()

    if p.returncode != 0:
        raise RuntimeError(f"DIRECT driver exit={p.returncode} stderr={se[:200]!r}")
    if not so:
        raise RuntimeError("DIRECT EMPTY_STDOUT")

    if not HEX_RE.match(so) or len(so) != EXPECTED_HEX_CHARS:
        raise RuntimeError(f"DIRECT BAD_HEX chars={len(so)} expected={EXPECTED_HEX_CHARS}")

    b = bytes.fromhex(so)
    if len(b) != EXPECTED_BYTES:
        raise RuntimeError(f"DIRECT BAD_LEN bytes={len(b)} expected={EXPECTED_BYTES}")

    h = hashlib.sha256(b).hexdigest()
    return so.lower(), b, h


def run_kem(mode: str, seed_hex: str | None):
    """
    Executa /app/kem_runner.
    - baseline: MODE=baseline, sem SEED_HEX
    - eaas/direct: MODE=qrng (para ativar RNG custom no kem_runner), com SEED_HEX
    """
    env = os.environ.copy()
    if mode == "baseline":
        env["MODE"] = "baseline"
        env.pop("SEED_HEX", None)
    else:
        # reusa o kem_runner já existente: ele ativa RNG custom quando MODE=qrng
        env["MODE"] = "qrng"
        env["SEED_HEX"] = seed_hex or ""

    t0 = time.perf_counter()
    p = subprocess.run(
        ["/app/kem_runner"],
        capture_output=True,
        text=True,
        timeout=20.0,
        check=False,
        env=env,
    )
    t1 = time.perf_counter()
    return p.returncode, (p.stdout or ""), (p.stderr or ""), (t1 - t0) * 1000.0


def main():
    api_url = os.environ.get("API_URL", f"http://127.0.0.1:8081/keys/pop?size_bits={SIZE_BITS}")
    reps = int(os.environ.get("REPS", "100"))
    warmup = int(os.environ.get("WARMUP", "5"))
    mode = os.environ.get("MODE", "both").strip().lower()
    csv_out = os.environ.get("CSV_OUT", "/out/results.csv")
    err_out = os.environ.get("ERR_OUT", "/out/errors.log")

    driver = os.environ.get("DRIVER_QRNG_DIRECT", "/app/qrng_direct.py")
    d_port = os.environ.get("DIRECT_PORT", "/dev/ttyACM0")
    d_baud = os.environ.get("DIRECT_BAUD", "115200")
    d_timeout = os.environ.get("DIRECT_TIMEOUT", "2.0")
    d_margin = os.environ.get("DIRECT_MARGIN_SAMPLES", "8")

    if mode == "both":
        modes = ["baseline", "eaas", "direct"]
    else:
        modes = [mode]

    for m in modes:
        if m not in ("baseline", "eaas", "direct"):
            print(f"MODE inválido: {mode}. Use baseline|eaas|direct|both", file=sys.stderr)
            return 2

    os.makedirs(os.path.dirname(csv_out), exist_ok=True)

    fieldnames = [
        "ts_utc", "rep", "mode",
        "api_url", "key_id",
        "entropy_bytes_len", "entropy_sha256",
        "t_seed_ms", "t_kem_ms", "t_total_ms",
        "exit_code", "error_code"
    ]

    with open(csv_out, "w", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=fieldnames).writeheader()

    with open(err_out, "w", encoding="utf-8") as f:
        f.write(f"# errors.log created_utc={utc_iso()}\n")
        f.write(f"# api_url={api_url}\n")
        f.write(f"# direct_driver={driver} port={d_port} baud={d_baud}\n")

    rep_id = 0

    for m in modes:
        # warmup (descarta)
        for _ in range(warmup):
            try:
                if m == "eaas":
                    fetch_entropy_from_eaas(api_url, timeout_s=5.0)
                elif m == "direct":
                    fetch_entropy_from_direct(driver, d_port, d_baud, d_timeout, d_margin)
                run_kem("baseline" if m == "baseline" else "seeded", "00" * 256 if m != "baseline" else None)
            except Exception as e:
                with open(err_out, "a", encoding="utf-8") as ferr:
                    ferr.write(f"{utc_iso()} WARMUP mode={m} err={e}\n")

        # reps
        for _ in range(reps):
            rep_id += 1
            ts = utc_iso()
            t_total0 = time.perf_counter()

            entropy_len = 0
            entropy_sha = ""
            key_id = ""
            seed_hex = None
            t_seed_ms = 0.0
            error_code = "OK"
            exit_code = ""

            try:
                if m == "eaas":
                    t0 = time.perf_counter()
                    seed_hex, b, entropy_sha, key_id = fetch_entropy_from_eaas(api_url, timeout_s=5.0)
                    t1 = time.perf_counter()
                    t_seed_ms = (t1 - t0) * 1000.0
                    entropy_len = len(b)

                elif m == "direct":
                    t0 = time.perf_counter()
                    seed_hex, b, entropy_sha = fetch_entropy_from_direct(driver, d_port, d_baud, d_timeout, d_margin)
                    t1 = time.perf_counter()
                    t_seed_ms = (t1 - t0) * 1000.0
                    entropy_len = len(b)

                # baseline não tem seed
                exit_code_i, so, se, t_kem_ms = run_kem("baseline" if m == "baseline" else "seeded", seed_hex)
                exit_code = str(exit_code_i)

                if exit_code_i != 0:
                    error_code = "KEM_FAIL"
                    raise RuntimeError(f"kem_runner exit={exit_code_i} stderr={se[:200]!r}")

                t_total1 = time.perf_counter()
                t_total_ms = (t_total1 - t_total0) * 1000.0

                with open(csv_out, "a", newline="", encoding="utf-8") as f:
                    w = csv.DictWriter(f, fieldnames=fieldnames)
                    w.writerow({
                        "ts_utc": ts,
                        "rep": rep_id,
                        "mode": m,
                        "api_url": api_url if m == "eaas" else "",
                        "key_id": key_id if m == "eaas" else "",
                        "entropy_bytes_len": entropy_len,
                        "entropy_sha256": entropy_sha,
                        "t_seed_ms": round(t_seed_ms, 3),
                        "t_kem_ms": round(t_kem_ms, 3),
                        "t_total_ms": round(t_total_ms, 3),
                        "exit_code": exit_code,
                        "error_code": "OK",
                    })

            except subprocess.TimeoutExpired:
                error_code = "TIMEOUT"
            except Exception as e:
                if error_code == "OK":
                    error_code = "OTHER"
                with open(csv_out, "a", newline="", encoding="utf-8") as f:
                    w = csv.DictWriter(f, fieldnames=fieldnames)
                    w.writerow({
                        "ts_utc": ts,
                        "rep": rep_id,
                        "mode": m,
                        "api_url": api_url if m == "eaas" else "",
                        "key_id": key_id if m == "eaas" else "",
                        "entropy_bytes_len": entropy_len,
                        "entropy_sha256": entropy_sha,
                        "t_seed_ms": round(t_seed_ms, 3),
                        "t_kem_ms": "",
                        "t_total_ms": "",
                        "exit_code": exit_code,
                        "error_code": error_code,
                    })
                with open(err_out, "a", encoding="utf-8") as ferr:
                    ferr.write(f"{utc_iso()} rep={rep_id} mode={m} error={error_code} detail={e}\n")

    print(f"[run] OK csv={csv_out} errors={err_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
