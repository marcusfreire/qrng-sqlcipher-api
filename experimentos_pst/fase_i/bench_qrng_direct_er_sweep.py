#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import math
import os
import re
import statistics as stats
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
DEFAULT_SIZES = [8,2048,4096,8192,12288,16384,19456,21504,24576,27648]
DEFAULT_ERS = [1, 3, 5]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_int_list(s: str) -> List[int]:
    parts = re.split(r"[,\s]+", s.strip())
    out: List[int] = []
    for p in parts:
        if p:
            out.append(int(p))
    return out


def classify_error(exit_code: int, timed_out: bool, stdout_text: str, stderr_text: str, size_bits: int) -> str:
    if timed_out:
        return "TIMEOUT"

    low = ((stdout_text or "") + "\n" + (stderr_text or "")).lower()
    if "memoryerror" in low or "memory allocation" in low or "out of memory" in low:
        return "MEMORY_ERROR"
    if "traceback" in low:
        return "TRACEBACK"
    if "repl" in low and ("não sincronizou" in low or "nao sincronizou" in low or "sync" in low):
        return "SYNC_FAIL"

    if exit_code != 0:
        return "NONZERO_EXIT"
    if stdout_text is None or not stdout_text.strip():
        return "EMPTY_STDOUT"

    hex_str = stdout_text.strip()
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
    er: int
    port: str
    baud: int
    timeout_s: float
    size_bits: int
    samples_requested: int
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
    er: int,
    size_bits: int,
    margin_samples: int,
    subprocess_slack_s: float,
) -> Tuple[RunResult, str, str]:
    samples_requested = math.ceil(size_bits / er) + margin_samples
    cmd = [
        sys.executable,
        driver,
        "--port", port,
        "--baud", str(baud),
        "--timeout", str(timeout_s),
        "--size-bits", str(size_bits),
        "--er", str(er),
        "--format", "hex",
        "--margin-samples", str(margin_samples),
    ]

    ts_start = utc_now_iso()
    t0 = time.perf_counter()
    timed_out = False
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s + subprocess_slack_s,
            check=False,
        )
        stdout_text = p.stdout or ""
        stderr_text = p.stderr or ""
        exit_code = int(p.returncode)
    except subprocess.TimeoutExpired as e:
        timed_out = True
        stdout_text = (e.stdout or "") if isinstance(e.stdout, str) else ""
        stderr_text = (e.stderr or "") if isinstance(e.stderr, str) else ""
        exit_code = 124

    total_time_s = max(0.0, time.perf_counter() - t0)
    ts_end = utc_now_iso()
    throughput_bps = (size_bits / total_time_s) if total_time_s > 0 else 0.0
    error_code = classify_error(exit_code, timed_out, stdout_text, stderr_text, size_bits)

    return RunResult(
        ts_start=ts_start,
        ts_end=ts_end,
        rep=0,
        er=er,
        port=port,
        baud=baud,
        timeout_s=timeout_s,
        size_bits=size_bits,
        samples_requested=samples_requested,
        margin_samples=margin_samples,
        total_time_s=total_time_s,
        throughput_bps=throughput_bps,
        exit_code=exit_code,
        stdout_chars=len(stdout_text),
        stderr_chars=len(stderr_text),
        error_code=error_code,
    ), stdout_text, stderr_text


def write_rows(path: str, fieldnames: List[str], rows: List[Dict[str, object]], mode: str = "a") -> None:
    with open(path, mode, newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if mode == "w":
            w.writeheader()
        for row in rows:
            w.writerow(row)


def aggregate_results(results: List[RunResult]) -> List[Dict[str, object]]:
    groups: Dict[Tuple[int, int], List[RunResult]] = {}
    for r in results:
        groups.setdefault((r.er, r.size_bits), []).append(r)

    rows: List[Dict[str, object]] = []
    for (er, size_bits), rs in sorted(groups.items()):
        ok = [r for r in rs if r.error_code == "OK"]
        first_error = next((r.error_code for r in rs if r.error_code != "OK"), "")
        rows.append({
            "er": er,
            "size_bits": size_bits,
            "samples_requested": rs[0].samples_requested,
            "n_runs": len(rs),
            "ok_runs": len(ok),
            "success_rate": len(ok) / len(rs) if rs else 0.0,
            "average_time_s": stats.mean([r.total_time_s for r in ok]) if ok else "",
            "median_time_s": stats.median([r.total_time_s for r in ok]) if ok else "",
            "average_throughput_bps": stats.mean([r.throughput_bps for r in ok]) if ok else "",
            "median_throughput_bps": stats.median([r.throughput_bps for r in ok]) if ok else "",
            "first_error": first_error,
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="Benchmark ER sweep do qrng_direct.py via REPL serial")
    ap.add_argument("--driver", required=True, help="Caminho do qrng_direct.py")
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--timeout", type=float, default=2.0, dest="timeout_s")
    ap.add_argument("--reps", type=int, default=10)
    ap.add_argument("--warmup", type=int, default=2)
    ap.add_argument("--out", required=True, help="CSV bruto de saída")
    ap.add_argument("--summary-out", default=None, help="CSV agregado. Padrão: <out>_summary.csv")
    ap.add_argument("--ers", default=",".join(map(str, DEFAULT_ERS)), help="Ex.: 1,3,5")
    ap.add_argument("--sizes", default=",".join(map(str, DEFAULT_SIZES)), help="Lista de size_bits")
    ap.add_argument("--margin-samples", type=int, default=8)
    ap.add_argument("--stop-after-failures", type=int, default=3,
                    help="Para cada ER, interrompe após N tamanhos consecutivos com falha total")
    ap.add_argument("--subprocess-slack", type=float, default=60.0,
                    help="Tempo extra para o subprocesso além de --timeout")
    args = ap.parse_args()

    driver = os.path.abspath(args.driver)
    if not os.path.exists(driver):
        print(f"[bench] ERRO: driver não existe: {driver}", file=sys.stderr)
        return 2

    ers = parse_int_list(args.ers)
    for er in ers:
        if er not in {1, 3, 5, 7}:
            print(f"[bench] ERRO: ER inválido {er}; permitido: 1,3,5,7", file=sys.stderr)
            return 2

    sizes = parse_int_list(args.sizes)
    for s in sizes:
        if s <= 0 or s % 8 != 0:
            print(f"[bench] ERRO: size_bits inválido {s}; deve ser positivo e múltiplo de 8", file=sys.stderr)
            return 2

    if args.margin_samples < 0:
        print("[bench] ERRO: --margin-samples deve ser >= 0", file=sys.stderr)
        return 2

    csv_path = os.path.abspath(args.out)
    summary_path = os.path.abspath(args.summary_out or (os.path.splitext(csv_path)[0] + "_summary.csv"))
    errlog_path = os.path.splitext(csv_path)[0] + "_errors.log"

    fieldnames = list(RunResult.__dataclass_fields__.keys())
    summary_fields = [
        "er", "size_bits", "samples_requested", "n_runs", "ok_runs", "success_rate",
        "average_time_s", "median_time_s", "average_throughput_bps", "median_throughput_bps", "first_error",
    ]

    write_rows(csv_path, fieldnames, [], mode="w")
    write_rows(summary_path, summary_fields, [], mode="w")
    with open(errlog_path, "w", encoding="utf-8") as ferr:
        ferr.write(f"# errors.log\n# created_utc={utc_now_iso()}\n# driver={driver}\n")

    print(f"[bench] driver={driver}", file=sys.stderr)
    print(f"[bench] ers={ers} sizes={sizes} warmup={args.warmup} reps={args.reps}", file=sys.stderr)

    all_results: List[RunResult] = []
    rep_global = 0

    for er in ers:
        consecutive_full_fail_sizes = 0
        print(f"[bench] === ER={er} ===", file=sys.stderr)
        for size_bits in sizes:
            # warmup descartado
            for _ in range(args.warmup):
                rr, _so, se = run_driver_once(
                    driver, args.port, args.baud, args.timeout_s, er, size_bits,
                    args.margin_samples, args.subprocess_slack,
                )
                if rr.error_code != "OK":
                    with open(errlog_path, "a", encoding="utf-8") as ferr:
                        ferr.write(
                            f"{utc_now_iso()} WARMUP er={er} size_bits={size_bits} "
                            f"samples={rr.samples_requested} exit={rr.exit_code} err={rr.error_code} "
                            f"stderr300={se[:300]!r}\n"
                        )

            size_results: List[RunResult] = []
            for r in range(args.reps):
                rep_global += 1
                rr, so, se = run_driver_once(
                    driver, args.port, args.baud, args.timeout_s, er, size_bits,
                    args.margin_samples, args.subprocess_slack,
                )
                rr.rep = rep_global
                size_results.append(rr)
                all_results.append(rr)
                write_rows(csv_path, fieldnames, [rr.__dict__], mode="a")

                if rr.error_code != "OK":
                    with open(errlog_path, "a", encoding="utf-8") as ferr:
                        ferr.write(
                            f"{utc_now_iso()} rep={rr.rep} er={er} size_bits={size_bits} "
                            f"samples={rr.samples_requested} exit={rr.exit_code} err={rr.error_code} "
                            f"stderr300={se[:300]!r}\n"
                        )

                print(
                    f"[bench] er={er} size_bits={size_bits} rep={r+1}/{args.reps} "
                    f"err={rr.error_code} time={rr.total_time_s:.3f}s thr={rr.throughput_bps:.1f} bps",
                    file=sys.stderr,
                )

            ok_runs = sum(1 for rr in size_results if rr.error_code == "OK")
            if ok_runs == 0:
                consecutive_full_fail_sizes += 1
            else:
                consecutive_full_fail_sizes = 0

            # atualiza resumo incremental
            summary_rows = aggregate_results(all_results)
            write_rows(summary_path, summary_fields, summary_rows, mode="w")

            if consecutive_full_fail_sizes >= args.stop_after_failures:
                print(
                    f"[bench] ER={er}: parada após {consecutive_full_fail_sizes} tamanhos consecutivos sem sucesso.",
                    file=sys.stderr,
                )
                break

    print(f"[bench] OK: raw={csv_path} summary={summary_path} errors={errlog_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
