#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import time
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Tuple

HEX_RE = re.compile(r"^[0-9a-fA-F]+$")

# DEFAULT_SIZES = [256] + list(range(1024, 65536 + 1, 1024))
DEFAULT_SIZES = list(range(27648, 65536 + 1, 1024))

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_int_list(s: str) -> List[int]:
    # aceita "256,512,1024" ou "256 512 1024"
    parts = re.split(r"[,\s]+", s.strip())
    out = []
    for p in parts:
        if not p:
            continue
        out.append(int(p))
    return out


def classify_error(
    exit_code: int,
    timed_out: bool,
    stdout_text: str,
    stderr_text: str,
    size_bits: int,
) -> str:
    if timed_out:
        return "TIMEOUT"
    if exit_code != 0:
        # tenta classificar melhor
        low = (stderr_text or "").lower()
        if "repl" in low and ("não sincronizou" in low or "nao sincronizou" in low or "sync" in low):
            return "SYNC_FAIL"
        return "NONZERO_EXIT"

    if stdout_text is None or len(stdout_text.strip()) == 0:
        return "EMPTY_STDOUT"

    hex_str = stdout_text.strip()
    # sanity: len esperado em hex: bits/4 (porque 1 hex = 4 bits)
    expected_hex_len = size_bits // 4
    if len(hex_str) != expected_hex_len:
        return "BAD_HEX"
    if not HEX_RE.match(hex_str):
        return "BAD_HEX"
    try:
        b = bytes.fromhex(hex_str)
    except Exception:
        return "BAD_HEX"
    if len(b) != size_bits // 8:
        return "BAD_HEX"

    return "OK"


@dataclass
class RunResult:
    ts_start: str
    ts_end: str
    rep: int
    port: str
    baud: int
    timeout_s: float
    size_bits: int
    margin_samples: int
    total_time_s: float
    throughput_bps: float
    exit_code: int
    stdout_chars: int
    stderr_chars: int
    error_code: str


def run_driver_once(
    driver: str,
    port: str,
    baud: int,
    timeout_s: float,
    size_bits: int,
    margin_samples: int,
) -> Tuple[RunResult, str, str]:
    """
    Retorna (RunResult, stdout, stderr).
    total_time_s mede do start do subprocess até retorno.
    """
    cmd = [
        sys.executable,  # python do ambiente
        driver,
        "--port", port,
        "--baud", str(baud),
        "--timeout", str(timeout_s),
        "--size-bits", str(size_bits),
        "--format", "hex",
        "--margin-samples", str(margin_samples),
        # ER permanece fixo em 5 pelo driver (padrão). Não passamos --er.
    ]

    ts_start = utc_now_iso()
    t0 = time.perf_counter()
    timed_out = False
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s + 60.0,  # “timeout total” do subprocess (inclui overhead)
            check=False,
        )
        stdout_text = p.stdout or ""
        stderr_text = p.stderr or ""
        exit_code = int(p.returncode)
    except subprocess.TimeoutExpired as e:
        timed_out = True
        stdout_text = (e.stdout or "") if isinstance(e.stdout, str) else ""
        stderr_text = (e.stderr or "") if isinstance(e.stderr, str) else ""
        exit_code = 124  # convenção
    t1 = time.perf_counter()
    ts_end = utc_now_iso()

    total_time_s = max(0.0, t1 - t0)
    throughput_bps = (size_bits / total_time_s) if total_time_s > 0 else 0.0

    error_code = classify_error(
        exit_code=exit_code,
        timed_out=timed_out,
        stdout_text=stdout_text,
        stderr_text=stderr_text,
        size_bits=size_bits,
    )

    rr = RunResult(
        ts_start=ts_start,
        ts_end=ts_end,
        rep=0,  # setado no loop
        port=port,
        baud=baud,
        timeout_s=timeout_s,
        size_bits=size_bits,
        margin_samples=margin_samples,
        total_time_s=total_time_s,
        throughput_bps=throughput_bps,
        exit_code=exit_code,
        stdout_chars=len(stdout_text),
        stderr_chars=len(stderr_text),
        error_code=error_code,
    )
    return rr, stdout_text, stderr_text


def main() -> int:
    ap = argparse.ArgumentParser(description="Benchmark do qrng_direct.py (ER fixo em 5)")
    ap.add_argument("--driver", required=True, help="Caminho do qrng_direct.py")
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--timeout", type=float, default=2.0, dest="timeout_s")
    ap.add_argument("--reps", type=int, default=30)
    ap.add_argument("--warmup", type=int, default=5)
    ap.add_argument("--out", required=True, help="CSV de saída (ex.: results.csv)")
    ap.add_argument("--sizes", default=",".join(map(str, DEFAULT_SIZES)),
                    help="Lista de size_bits (ex.: 256,512,1024,...)")
    ap.add_argument("--margin-samples", type=int, default=8)
    ap.add_argument("--margins", default=None,
                    help="Opcional: varrer margin_samples (ex.: 0,4,8,16). Se setado, ignora --margin-samples.")
    args = ap.parse_args()

    driver = os.path.abspath(args.driver)
    if not os.path.exists(driver):
        print(f"[bench] ERRO: driver não existe: {driver}", file=sys.stderr)
        return 2

    sizes = parse_int_list(args.sizes)
    # valida sizes (positivo e múltiplo de 8)
    for s in sizes:
        if s <= 0 or (s % 8) != 0:
            print(f"[bench] ERRO: size_bits inválido: {s} (exige positivo e múltiplo de 8)", file=sys.stderr)
            return 2

    if args.margins:
        margins = parse_int_list(args.margins)
        for m in margins:
            if m < 0:
                print(f"[bench] ERRO: margin_samples inválido: {m} (exige >=0)", file=sys.stderr)
                return 2
    else:
        margins = [int(args.margin_samples)]

    csv_path = os.path.abspath(args.out)
    errlog_path = os.path.splitext(csv_path)[0] + "_errors.log"

    fieldnames = [
        "ts_start", "ts_end", "rep", "port", "baud", "timeout_s", "size_bits", "margin_samples",
        "total_time_s", "throughput_bps", "exit_code", "stdout_chars", "stderr_chars", "error_code"
    ]

    # escreve cabeçalho CSV
    with open(csv_path, "w", newline="", encoding="utf-8") as fcsv:
        w = csv.DictWriter(fcsv, fieldnames=fieldnames)
        w.writeheader()

    # abre error log
    with open(errlog_path, "w", encoding="utf-8") as ferr:
        ferr.write(f"# errors.log\n# created_utc={utc_now_iso()}\n# driver={driver}\n")

    total_points = len(sizes) * len(margins)
    print(f"[bench] driver={driver}", file=sys.stderr)
    print(f"[bench] points={total_points} sizes={sizes} margins={margins} warmup={args.warmup} reps={args.reps}", file=sys.stderr)

    rep_global = 0
    for margin in margins:
        for size_bits in sizes:
            # warmup (descarta)
            for i in range(args.warmup):
                rr, so, se = run_driver_once(
                    driver=driver,
                    port=args.port,
                    baud=args.baud,
                    timeout_s=args.timeout_s,
                    size_bits=size_bits,
                    margin_samples=margin,
                )
                # não grava CSV do warmup; apenas loga se falhou muito
                if rr.error_code != "OK":
                    with open(errlog_path, "a", encoding="utf-8") as ferr:
                        ferr.write(
                            f"{utc_now_iso()} WARMUP size_bits={size_bits} margin={margin} "
                            f"exit={rr.exit_code} err={rr.error_code} stderr200={se[:200]!r}\n"
                        )

            # reps (grava)
            for r in range(args.reps):
                rep_global += 1
                rr, so, se = run_driver_once(
                    driver=driver,
                    port=args.port,
                    baud=args.baud,
                    timeout_s=args.timeout_s,
                    size_bits=size_bits,
                    margin_samples=margin,
                )
                rr.rep = rep_global

                # sanity check obrigatório: size_bits=256, hex len=64, bytes=32 (se OK)
                if size_bits == 256 and rr.error_code == "OK":
                    hex_str = so.strip()
                    if len(hex_str) != 64:
                        rr.error_code = "BAD_HEX"
                    else:
                        try:
                            b = bytes.fromhex(hex_str)
                            if len(b) != 32:
                                rr.error_code = "BAD_HEX"
                        except Exception:
                            rr.error_code = "BAD_HEX"

                # escreve CSV
                with open(csv_path, "a", newline="", encoding="utf-8") as fcsv:
                    w = csv.DictWriter(fcsv, fieldnames=fieldnames)
                    w.writerow(rr.__dict__)

                # erros no log
                if rr.error_code != "OK":
                    with open(errlog_path, "a", encoding="utf-8") as ferr:
                        ferr.write(
                            f"{utc_now_iso()} rep={rr.rep} size_bits={size_bits} margin={margin} "
                            f"exit={rr.exit_code} err={rr.error_code} stderr200={se[:200]!r}\n"
                        )

                # progresso leve
                if (r + 1) % max(1, args.reps // 3) == 0:
                    print(
                        f"[bench] size_bits={size_bits} margin={margin} rep={r+1}/{args.reps} last_err={rr.error_code}",
                        file=sys.stderr,
                    )

    print(f"[bench] OK: csv={csv_path} errors={errlog_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
