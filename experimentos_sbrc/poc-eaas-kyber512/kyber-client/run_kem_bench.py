#!/usr/bin/env python3
from __future__ import annotations

import base64
import csv
import hashlib
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
import json


HEX_RE = re.compile(r"^[0-9a-fA-F]+$")

def utc_iso():
    return datetime.now(timezone.utc).isoformat()

def fetch_entropy_hex(api_url: str, timeout_s: float = 5.0) -> tuple[str, bytes, str]:
    """
    Retorna (entropy_hex_512, entropy_bytes_256, sha256_hex).

    Espera JSON da API contendo, no mínimo:
      - slice_hex (preferido) OU slice_b64
      - size_bits == 2048 (opcional, mas validado aqui para auditoria)
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
        raise RuntimeError(f"curl nonzero exit={p.returncode} stderr={stderr[:200]!r}")
    if not stdout:
        raise RuntimeError("EMPTY_STDOUT")

    # Parse JSON
    try:
        obj = json.loads(stdout)
    except Exception as e:
        raise RuntimeError(f"BAD_JSON stdout_len={len(stdout)} err={e}")

    # (Opcional, mas recomendado): valida que size_bits bate com o endpoint
    size_bits = obj.get("size_bits")
    if size_bits is not None and int(size_bits) != 2048:
        raise RuntimeError(f"BAD_SIZE_BITS got={size_bits} expected=2048")

    # Preferir slice_hex (mais direto e auditável)
    slice_hex = obj.get("slice_hex")
    if isinstance(slice_hex, str) and slice_hex:
        hex_str = slice_hex.strip()

        if not HEX_RE.match(hex_str) or (len(hex_str) % 2 != 0):
            raise RuntimeError(f"BAD_HEX_FIELD len={len(hex_str)}")

        b = bytes.fromhex(hex_str)
        if len(b) != 256:
            raise RuntimeError(f"BAD_LEN_HEX bytes={len(b)} expected=256 chars={len(hex_str)}")

        h = hashlib.sha256(b).hexdigest()
        return hex_str.lower(), b, h

    # Fallback: slice_b64
    slice_b64 = obj.get("slice_b64")
    if isinstance(slice_b64, str) and slice_b64:
        try:
            b = base64.b64decode(slice_b64.strip(), validate=True)
        except Exception as e:
            raise RuntimeError(f"BAD_B64_FIELD err={e}")

        if len(b) != 256:
            raise RuntimeError(f"BAD_LEN_B64 bytes={len(b)} expected=256")

        h = hashlib.sha256(b).hexdigest()
        return b.hex(), b, h

    raise RuntimeError("MISSING_FIELDS: expected slice_hex or slice_b64")


def run_kem(mode: str, seed_hex: str | None) -> tuple[int, str, str, float]:
    """
    Executa kem_runner e retorna (exit_code, stdout, stderr, t_ms).
    """
    env = os.environ.copy()
    env["MODE"] = mode
    if mode == "qrng":
        env["SEED_HEX"] = seed_hex or ""
    t0 = time.perf_counter()
    p = subprocess.run(
        ["/app/kem_runner"],
        capture_output=True,
        text=True,
        timeout=10.0,
        check=False,
        env=env,
    )
    t1 = time.perf_counter()
    return p.returncode, (p.stdout or ""), (p.stderr or ""), (t1 - t0) * 1000.0

def main():
    api_url = os.environ.get("API_URL", "http://127.0.0.1:8081/keys/pop?size_bits=2048")
    reps = int(os.environ.get("REPS", "100"))
    warmup = int(os.environ.get("WARMUP", "5"))
    mode = os.environ.get("MODE", "both").strip().lower()
    csv_out = os.environ.get("CSV_OUT", "/out/results.csv")
    err_out = os.environ.get("ERR_OUT", "/out/errors.log")

    # ER fixo em 5: não é configurado aqui. A API fornece 2048 bits.
    # O objetivo é PoC e métricas de latência/estabilidade.

    modes = ["baseline", "qrng"] if mode == "both" else [mode]
    if any(m not in ("baseline", "qrng") for m in modes):
        print(f"MODE inválido: {mode}", file=sys.stderr)
        return 2

    os.makedirs(os.path.dirname(csv_out), exist_ok=True)

    fieldnames = [
        "key_id",
        "ts_utc", "rep", "mode",
        "api_url",
        "entropy_bytes_len", "entropy_sha256",
        "t_http_ms", "t_kem_ms", "t_total_ms",
        "exit_code", "error_code"
    ]

    with open(csv_out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()

    with open(err_out, "w", encoding="utf-8") as f:
        f.write(f"# errors.log created_utc={utc_iso()} api_url={api_url}\n")

    rep_id = 0
    for m in modes:
        # warmup
        for i in range(warmup):
            try:
                seed_hex = None
                t_http_ms = 0.0
                sha = ""
                if m == "qrng":
                    t0 = time.perf_counter()
                    seed_hex, b, sha = fetch_entropy_hex(api_url, timeout_s=5.0)
                    t1 = time.perf_counter()
                    t_http_ms = (t1 - t0) * 1000.0
                run_kem(m, seed_hex)
            except Exception as e:
                with open(err_out, "a", encoding="utf-8") as ferr:
                    ferr.write(f"{utc_iso()} WARMUP mode={m} err={e}\n")

        # reps
        for r in range(reps):
            rep_id += 1
            ts = utc_iso()
            t_total0 = time.perf_counter()

            entropy_len = 0
            entropy_sha = ""
            seed_hex = None
            t_http_ms = 0.0
            error_code = "OK"

            try:
                if m == "qrng":
                    t0 = time.perf_counter()
                    seed_hex, b, entropy_sha = fetch_entropy_hex(api_url, timeout_s=5.0)
                    t1 = time.perf_counter()
                    t_http_ms = (t1 - t0) * 1000.0
                    entropy_len = len(b)
                    if entropy_len != 256:
                        raise RuntimeError(f"BAD_ENTROPY_LEN {entropy_len}")
                else:
                    entropy_len = 0
                    entropy_sha = ""

                exit_code, so, se, t_kem_ms = run_kem(m, seed_hex)
                if exit_code != 0:
                    error_code = "KEM_FAIL"
                    raise RuntimeError(f"kem_runner exit={exit_code} stderr={se[:200]!r}")

                t_total1 = time.perf_counter()
                t_total_ms = (t_total1 - t_total0) * 1000.0

                with open(csv_out, "a", newline="", encoding="utf-8") as f:
                    w = csv.DictWriter(f, fieldnames=fieldnames)
                    w.writerow({
                        "ts_utc": ts,
                        "rep": rep_id,
                        "mode": m,
                        "api_url": api_url,
                        "entropy_bytes_len": entropy_len,
                        "entropy_sha256": entropy_sha,
                        "t_http_ms": round(t_http_ms, 3),
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
                        "api_url": api_url,
                        "entropy_bytes_len": entropy_len,
                        "entropy_sha256": entropy_sha,
                        "t_http_ms": round(t_http_ms, 3),
                        "t_kem_ms": "",
                        "t_total_ms": "",
                        "exit_code": "",
                        "error_code": error_code,
                    })

                with open(err_out, "a", encoding="utf-8") as ferr:
                    ferr.write(f"{utc_iso()} rep={rep_id} mode={m} error={error_code} detail={e}\n")

    print(f"[run] OK csv={csv_out} errors={err_out}", file=sys.stderr)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())