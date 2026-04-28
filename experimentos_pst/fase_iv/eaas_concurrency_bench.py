#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import os
import random
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


WORKLOADS = {
    "A": lambda rng: 2048,
    "B": lambda rng: 256,
    "C": lambda rng: 256 if rng.random() < 0.8 else 2048,
}

DEFAULT_CONCURRENCY = [1, 2, 4, 8, 16, 32, 64]


@dataclass
class RequestRecord:
    run_id: str
    workload: str
    concurrency: int
    repeat: int
    client_id: int
    request_index: int
    start_wall_time: str
    end_wall_time: str
    start_mono: float
    end_mono: float
    latency_ms: float
    status_code: int
    ok: int
    timeout: int
    error_type: str
    key_id: str
    requested_size_bits: int
    returned_size_bits: int
    discarded_bits: int
    response_text: str


@dataclass
class BacklogSample:
    run_id: str
    workload: str
    concurrency: int
    repeat: int
    t_rel_s: float
    in_flight: int
    completed: int
    success: int
    failures: int


class MockEaaSTransport(httpx.AsyncBaseTransport):
    """In-memory mock transport for quick tests.

    Simulates atomic block consumption from a finite pool of 2048-bit blocks.
    """

    def __init__(self, initial_blocks: int = 4000, base_delay_ms: float = 3.0):
        self._lock = asyncio.Lock()
        self.remaining_blocks = initial_blocks
        self.next_key = 0
        self.base_delay_ms = base_delay_ms

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        q = dict(request.url.params)
        try:
            size_bits = int(q.get("size_bits", "2048"))
        except ValueError:
            size_bits = 2048

        await asyncio.sleep((self.base_delay_ms + (size_bits / 2048.0)) / 1000.0)

        async with self._lock:
            if self.remaining_blocks <= 0:
                body = {"detail": "entropy pool exhausted"}
                return httpx.Response(404, json=body, request=request)

            key_id = f"mock-{self.next_key:08d}"
            self.next_key += 1
            self.remaining_blocks -= 1

        hex_len = max(1, size_bits // 4)
        body = {
            "key_id": key_id,
            "slice_hex": ("ab" * ((hex_len + 1) // 2))[:hex_len],
            "size_bits": size_bits,
        }
        return httpx.Response(200, json=body, request=request)


class MetricsState:
    def __init__(self) -> None:
        self.in_flight = 0
        self.completed = 0
        self.success = 0
        self.failures = 0
        self.lock = asyncio.Lock()

    async def inc_inflight(self) -> None:
        async with self.lock:
            self.in_flight += 1

    async def dec_inflight(self, ok: bool) -> Tuple[int, int, int, int]:
        async with self.lock:
            self.in_flight -= 1
            self.completed += 1
            if ok:
                self.success += 1
            else:
                self.failures += 1
            return self.in_flight, self.completed, self.success, self.failures

    async def snapshot(self) -> Tuple[int, int, int, int]:
        async with self.lock:
            return self.in_flight, self.completed, self.success, self.failures


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def percentile(values: Iterable[float], q: float) -> Optional[float]:
    vals = [float(v) for v in values if pd.notna(v)]
    if not vals:
        return None
    return float(np.percentile(vals, q))



def bootstrap_ci(values: List[float], stat_fn=np.median, n_boot: int = 2000, alpha: float = 0.05, seed: int = 1234) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    clean = np.array([float(v) for v in values if pd.notna(v)], dtype=float)
    if clean.size == 0:
        return None, None, None
    if clean.size == 1:
        s = float(stat_fn(clean))
        return s, s, s
    rng = np.random.default_rng(seed)
    stats = []
    for _ in range(n_boot):
        sample = rng.choice(clean, size=clean.size, replace=True)
        stats.append(float(stat_fn(sample)))
    lo = float(np.quantile(stats, alpha / 2.0))
    hi = float(np.quantile(stats, 1.0 - alpha / 2.0))
    mid = float(stat_fn(clean))
    return mid, lo, hi



def jain_index(xs: List[float]) -> Optional[float]:
    arr = np.array(xs, dtype=float)
    if arr.size == 0:
        return None
    denom = arr.size * np.sum(arr ** 2)
    if denom == 0:
        return 0.0
    return float((np.sum(arr) ** 2) / denom)



def safe_json(resp: httpx.Response) -> Dict[str, Any]:
    try:
        return resp.json()
    except Exception:
        return {}



def infer_returned_size_bits(body: Dict[str, Any], requested_size_bits: int) -> int:
    if isinstance(body.get("size_bits"), int):
        return int(body["size_bits"])
    if isinstance(body.get("slice_hex"), str):
        return len(body["slice_hex"]) * 4
    return requested_size_bits


async def worker(
    *,
    client: httpx.AsyncClient,
    base_url: str,
    endpoint: str,
    client_id: int,
    workload: str,
    concurrency: int,
    repeat: int,
    run_id: str,
    deadline: float,
    stop_on_exhaustion: bool,
    stop_event: asyncio.Event,
    queue: asyncio.Queue,
    state: MetricsState,
    seed: int,
) -> None:
    rng = random.Random(seed + client_id)
    request_index = 0
    while not stop_event.is_set() and time.perf_counter() < deadline:
        requested = WORKLOADS[workload](rng)
        start_wall = utc_now_iso()
        start_mono = time.perf_counter()
        await state.inc_inflight()
        status_code = 0
        ok = 0
        timeout = 0
        key_id = ""
        returned = 0
        error_type = ""
        response_text = ""
        try:
            resp = await client.post(f"{base_url.rstrip('/')}{endpoint}", params={"size_bits": requested})
            status_code = resp.status_code
            body = safe_json(resp)
            response_text = resp.text[:500]
            if 200 <= resp.status_code < 300:
                ok = 1
                key_id = str(body.get("key_id", ""))
                returned = infer_returned_size_bits(body, requested)
            else:
                error_type = f"http_{resp.status_code}"
                if stop_on_exhaustion and 400 <= resp.status_code < 500:
                    stop_event.set()
        except httpx.TimeoutException:
            timeout = 1
            error_type = "timeout"
        except Exception as exc:  # pragma: no cover
            error_type = exc.__class__.__name__
        finally:
            end_mono = time.perf_counter()
            end_wall = utc_now_iso()
            latency_ms = (end_mono - start_mono) * 1000.0
            await state.dec_inflight(ok=bool(ok))
            await queue.put(
                RequestRecord(
                    run_id=run_id,
                    workload=workload,
                    concurrency=concurrency,
                    repeat=repeat,
                    client_id=client_id,
                    request_index=request_index,
                    start_wall_time=start_wall,
                    end_wall_time=end_wall,
                    start_mono=start_mono,
                    end_mono=end_mono,
                    latency_ms=latency_ms,
                    status_code=status_code,
                    ok=ok,
                    timeout=timeout,
                    error_type=error_type,
                    key_id=key_id,
                    requested_size_bits=requested,
                    returned_size_bits=returned,
                    discarded_bits=(2048 - returned) if ok else 0,
                    response_text=response_text,
                )
            )
            request_index += 1


async def backlog_sampler(
    *,
    run_id: str,
    workload: str,
    concurrency: int,
    repeat: int,
    state: MetricsState,
    stop_event: asyncio.Event,
    queue: asyncio.Queue,
    start_mono: float,
    interval_s: float = 0.2,
) -> None:
    while not stop_event.is_set():
        inflight, completed, success, failures = await state.snapshot()
        await queue.put(
            BacklogSample(
                run_id=run_id,
                workload=workload,
                concurrency=concurrency,
                repeat=repeat,
                t_rel_s=time.perf_counter() - start_mono,
                in_flight=inflight,
                completed=completed,
                success=success,
                failures=failures,
            )
        )
        await asyncio.sleep(interval_s)
    inflight, completed, success, failures = await state.snapshot()
    await queue.put(
        BacklogSample(
            run_id=run_id,
            workload=workload,
            concurrency=concurrency,
            repeat=repeat,
            t_rel_s=time.perf_counter() - start_mono,
            in_flight=inflight,
            completed=completed,
            success=success,
            failures=failures,
        )
    )


async def drain_queue(q: asyncio.Queue) -> List[Any]:
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    return items


async def run_single_configuration(
    *,
    base_url: str,
    endpoint: str,
    workload: str,
    concurrency: int,
    repeat: int,
    duration_s: float,
    stop_on_exhaustion: bool,
    timeout_s: float,
    max_connections: int,
    transport_mode: str,
    mock_blocks: int,
    seed: int,
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    run_id = f"{workload}_N{concurrency}_r{repeat}"
    request_queue: asyncio.Queue = asyncio.Queue()
    backlog_queue: asyncio.Queue = asyncio.Queue()
    stop_event = asyncio.Event()
    state = MetricsState()
    start_mono = time.perf_counter()
    deadline = start_mono + duration_s

    transport = None
    if transport_mode == "mock":
        transport = MockEaaSTransport(initial_blocks=mock_blocks)

    timeout = httpx.Timeout(timeout_s)
    limits = httpx.Limits(max_connections=max_connections, max_keepalive_connections=max_connections)

    async with httpx.AsyncClient(timeout=timeout, limits=limits, transport=transport) as client:
        sampler_task = asyncio.create_task(
            backlog_sampler(
                run_id=run_id,
                workload=workload,
                concurrency=concurrency,
                repeat=repeat,
                state=state,
                stop_event=stop_event,
                queue=backlog_queue,
                start_mono=start_mono,
            )
        )

        worker_tasks = [
            asyncio.create_task(
                worker(
                    client=client,
                    base_url=base_url,
                    endpoint=endpoint,
                    client_id=i,
                    workload=workload,
                    concurrency=concurrency,
                    repeat=repeat,
                    run_id=run_id,
                    deadline=deadline,
                    stop_on_exhaustion=stop_on_exhaustion,
                    stop_event=stop_event,
                    queue=request_queue,
                    state=state,
                    seed=seed,
                )
            )
            for i in range(concurrency)
        ]

        await asyncio.gather(*worker_tasks)
        stop_event.set()
        await sampler_task

    req_items = [asdict(x) for x in await drain_queue(request_queue)]
    backlog_items = [asdict(x) for x in await drain_queue(backlog_queue)]
    req_df = pd.DataFrame(req_items)
    backlog_df = pd.DataFrame(backlog_items)

    if req_df.empty:
        req_df = pd.DataFrame(columns=[f.name for f in RequestRecord.__dataclass_fields__.values()])
    if backlog_df.empty:
        backlog_df = pd.DataFrame(columns=[f.name for f in BacklogSample.__dataclass_fields__.values()])

    summary = summarize_single_run(req_df, backlog_df, duration_s)
    summary.update({
        "run_id": run_id,
        "workload": workload,
        "concurrency": concurrency,
        "repeat": repeat,
        "duration_s": duration_s,
    })
    return req_df, backlog_df, summary



def summarize_single_run(req_df: pd.DataFrame, backlog_df: pd.DataFrame, duration_s: float) -> Dict[str, Any]:
    total = int(len(req_df))
    success_df = req_df[req_df["ok"] == 1].copy() if not req_df.empty else pd.DataFrame()
    success = int(len(success_df))
    lat = success_df["latency_ms"].tolist() if not success_df.empty else []
    by_client = success_df.groupby("client_id").size().reindex(sorted(req_df["client_id"].unique()) if not req_df.empty else [], fill_value=0)
    key_counts = Counter([k for k in success_df.get("key_id", []) if isinstance(k, str) and k])
    duplicate_ids = sorted([k for k, c in key_counts.items() if c > 1])
    http_4xx = int(((req_df["status_code"] >= 400) & (req_df["status_code"] < 500)).sum()) if not req_df.empty else 0
    http_5xx = int(((req_df["status_code"] >= 500) & (req_df["status_code"] < 600)).sum()) if not req_df.empty else 0
    timeouts = int(req_df["timeout"].sum()) if not req_df.empty else 0
    bits_returned = int(success_df["returned_size_bits"].sum()) if not success_df.empty else 0
    keys_consumed = int(success)
    total_capacity_bits = keys_consumed * 2048
    waste_ratio = None if total_capacity_bits == 0 else float(1.0 - (bits_returned / total_capacity_bits))
    runtime = duration_s
    if not req_df.empty:
        runtime = max(duration_s, float(req_df["end_mono"].max() - req_df["start_mono"].min()))

    summary = {
        "requests_total": total,
        "requests_success": success,
        "latency_p50_ms": percentile(lat, 50),
        "latency_p95_ms": percentile(lat, 95),
        "latency_p99_ms": percentile(lat, 99),
        "throughput_req_s": None if runtime <= 0 else total / runtime,
        "throughput_effective_bits_s": None if runtime <= 0 else bits_returned / runtime,
        "exhaustion_keys_s": None if runtime <= 0 else keys_consumed / runtime,
        "http_4xx_pct": None if total == 0 else 100.0 * http_4xx / total,
        "http_5xx_pct": None if total == 0 else 100.0 * http_5xx / total,
        "timeout_pct": None if total == 0 else 100.0 * timeouts / total,
        "duplicates": len(duplicate_ids),
        "duplicate_ids": json.dumps(duplicate_ids[:20]),
        "fairness_jain": jain_index(by_client.tolist()),
        "waste_ratio": waste_ratio,
        "backlog_peak": int(backlog_df["in_flight"].max()) if not backlog_df.empty else 0,
        "backlog_mean": float(backlog_df["in_flight"].mean()) if not backlog_df.empty else 0.0,
        "bits_returned_total": bits_returned,
        "keys_consumed_total": keys_consumed,
        "runtime_s": runtime,
    }
    return summary



def aggregate_summaries(summary_df: pd.DataFrame, req_df: pd.DataFrame) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for (workload, concurrency), grp in summary_df.groupby(["workload", "concurrency"]):
        row: Dict[str, Any] = {"workload": workload, "concurrency": concurrency}
        for metric in [
            "latency_p50_ms",
            "latency_p95_ms",
            "latency_p99_ms",
            "throughput_req_s",
            "throughput_effective_bits_s",
            "exhaustion_keys_s",
            "fairness_jain",
            "http_4xx_pct",
            "http_5xx_pct",
            "timeout_pct",
            "backlog_peak",
            "backlog_mean",
            "waste_ratio",
            "duplicates",
        ]:
            vals = grp[metric].dropna().tolist()
            med, lo, hi = bootstrap_ci(vals, stat_fn=np.median)
            row[f"{metric}_median"] = med
            row[f"{metric}_ci95_lo"] = lo
            row[f"{metric}_ci95_hi"] = hi

        sub = req_df[(req_df["workload"] == workload) & (req_df["concurrency"] == concurrency) & (req_df["ok"] == 1)]
        row["latency_p95_aggregated_ms"] = percentile(sub["latency_ms"].tolist(), 95) if not sub.empty else None
        row["latency_p99_aggregated_ms"] = percentile(sub["latency_ms"].tolist(), 99) if not sub.empty else None
        row["duplicates_total"] = int(grp["duplicates"].sum())
        rows.append(row)
    return pd.DataFrame(rows).sort_values(["workload", "concurrency"]) if rows else pd.DataFrame()



def detect_non_linear_latency(agg_df: pd.DataFrame) -> List[str]:
    notes: List[str] = []
    for workload, grp in agg_df.groupby("workload"):
        grp = grp.sort_values("concurrency")
        prev_lat = None
        prev_n = None
        for _, row in grp.iterrows():
            lat = row.get("latency_p50_ms_median")
            n = row.get("concurrency")
            if prev_lat is not None and lat is not None and prev_n is not None and n == 2 * prev_n:
                scaling = lat / max(prev_lat, 1e-9)
                if scaling > 1.6:
                    notes.append(f"Carga {workload}: crescimento não linear de latência a partir de N={int(n)} (fator {scaling:.2f} na p50 ao dobrar a concorrência).")
                    break
            prev_lat, prev_n = lat, n
    return notes



def infer_db_contention(agg_df: pd.DataFrame) -> List[str]:
    notes: List[str] = []
    for workload, grp in agg_df.groupby("workload"):
        grp = grp.sort_values("concurrency")
        thr = grp["throughput_req_s_median"].tolist()
        lat = grp["latency_p95_ms_median"].tolist()
        backlog = grp["backlog_peak_median"].tolist()
        ns = grp["concurrency"].tolist()
        if len(thr) >= 3:
            max_thr = max(v for v in thr if v is not None)
            for i in range(1, len(thr)):
                if thr[i] is not None and thr[i-1] is not None and lat[i] is not None and lat[i-1] is not None:
                    near_plateau = thr[i] >= 0.9 * max_thr and thr[i-1] >= 0.75 * max_thr
                    latency_jump = lat[i] > 1.5 * max(lat[i-1], 1e-9)
                    backlog_up = (backlog[i] or 0) >= (backlog[i-1] or 0)
                    if near_plateau and latency_jump and backlog_up:
                        notes.append(f"Inferência: há evidência compatível com contenção em armazenamento/serialização interna na carga {workload} a partir de N={int(ns[i])}, pois o throughput entra em platô enquanto a p95 e o backlog aumentam.")
                        break
    return notes



def summarize_exhaustion_behavior(req_df: pd.DataFrame) -> List[str]:
    notes: List[str] = []
    if req_df.empty:
        return notes
    for (workload, concurrency), grp in req_df.groupby(["workload", "concurrency"]):
        four_xx = grp[(grp["status_code"] >= 400) & (grp["status_code"] < 500)]
        if four_xx.empty:
            continue
        first_fail = float(four_xx["start_mono"].min())
        before = grp[(grp["ok"] == 1) & (grp["end_mono"] <= first_fail)]
        after = grp[(grp["ok"] == 1) & (grp["start_mono"] >= first_fail)]
        before_p95 = percentile(before["latency_ms"].tolist(), 95) if not before.empty else None
        after_p95 = percentile(after["latency_ms"].tolist(), 95) if not after.empty else None
        if before_p95 is not None and after_p95 is not None:
            notes.append(f"Próximo da exaustão. carga {workload}, N={concurrency}. a p95 passou de {before_p95:.2f} ms antes da primeira resposta 4xx para {after_p95:.2f} ms depois desse ponto.")
    return notes



def generate_text_analysis(agg_df: pd.DataFrame, summary_df: pd.DataFrame, req_df: pd.DataFrame) -> str:
    lines: List[str] = []
    dup_total = int(summary_df["duplicates"].sum()) if not summary_df.empty else 0
    if dup_total == 0:
        lines.append("Unicidade. Não houve duplicação de key_id nas execuções observadas, o que é consistente com semântica de consumo atômico sob concorrência.")
    else:
        dup_rows = summary_df[summary_df["duplicates"] > 0][["workload", "concurrency", "repeat", "duplicates"]]
        lines.append(f"Unicidade. Houve violação de unicidade. total de duplicatas detectadas: {dup_total}. configurações afetadas: {dup_rows.to_dict(orient='records')[:10]}.")

    nl = detect_non_linear_latency(agg_df)
    lines.extend(nl if nl else ["Latência. Não foi identificado ponto inequívoco de crescimento não linear com o critério automático adotado."])

    for workload in ["A", "B", "C"]:
        sub = agg_df[agg_df["workload"] == workload]
        if sub.empty:
            continue
        best = sub.sort_values("throughput_effective_bits_s_median", ascending=False).iloc[0]
        lines.append(
            f"Carga {workload}. melhor ponto em bits/s ocorreu em N={int(best['concurrency'])}, com mediana de {best['throughput_effective_bits_s_median']:.2f} bits/s e fairness de {best['fairness_jain_median']:.3f}."
        )

    waste = agg_df[["workload", "concurrency", "waste_ratio_median"]].dropna()
    if not waste.empty:
        b = waste[waste["workload"] == "B"]
        c = waste[waste["workload"] == "C"]
        a = waste[waste["workload"] == "A"]
        if not b.empty:
            lines.append(f"Desperdício por requisição de 256 bits. Na carga B, a mediana do desperdício de bloco foi {100*b['waste_ratio_median'].median():.2f}%, valor esperado quando um bloco inteiro de 2048 bits é consumido para entregar 256 bits.")
        if not c.empty:
            lines.append(f"Carga mista. Na carga C, o desperdício mediano foi {100*c['waste_ratio_median'].median():.2f}%, refletindo a combinação de pedidos curtos e longos.")
        if not a.empty:
            lines.append(f"Referência. Na carga A, o desperdício mediano foi {100*a['waste_ratio_median'].median():.2f}%, próximo de zero quando o cliente solicita o bloco completo.")

    lines.extend(infer_db_contention(agg_df) or ["Contenção. Não há evidência automática conclusiva de contenção em banco com o critério heurístico adotado. Isso não prova ausência de contenção."])
    lines.extend(summarize_exhaustion_behavior(req_df) or ["Exaustão. Não houve respostas 4xx suficientes para caracterizar comportamento próximo da exaustão nas execuções observadas."])

    # workload comparison at highest common concurrency
    common = agg_df.groupby("concurrency").size()
    if not common.empty:
        n = int(common.index.max())
        sub = agg_df[agg_df["concurrency"] == n]
        if set(sub["workload"]) >= {"A", "B", "C"}:
            ranking = sub.sort_values("throughput_effective_bits_s_median", ascending=False)[["workload", "throughput_effective_bits_s_median"]]
            ranks = ", ".join([f"{r.workload}={r.throughput_effective_bits_s_median:.2f} bits/s" for r in ranking.itertuples()])
            lines.append(f"Comparação entre cargas em N={n}. ranking por bits/s efetivos: {ranks}.")

    return "\n".join(lines)



def save_plot(df: pd.DataFrame, x: str, ys: List[Tuple[str, str]], outpath: Path, title: str, ylabel: str) -> None:
    plt.figure(figsize=(8, 5))
    for workload, grp in df.groupby("workload"):
        grp = grp.sort_values(x)
        for col, suffix in ys:
            if col in grp:
                plt.plot(grp[x], grp[col], marker="o", label=f"{workload} {suffix}")
    plt.xscale("log", base=2)
    plt.xticks(sorted(df[x].unique()), sorted(df[x].unique()))
    plt.xlabel("Concorrência (N)")
    plt.ylabel(ylabel)
    plt.title(title)
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(outpath)
    plt.close()



def save_time_series(req_df: pd.DataFrame, backlog_df: pd.DataFrame, outdir: Path) -> None:
    if req_df.empty:
        return
    for workload in sorted(req_df["workload"].unique()):
        sub_req = req_df[(req_df["workload"] == workload) & (req_df["ok"] == 1)]
        sub_back = backlog_df[backlog_df["workload"] == workload]
        if sub_req.empty:
            continue
        # use highest concurrency first repeat for visual inspection
        target_n = int(sub_req["concurrency"].max())
        run_ids = sub_req[sub_req["concurrency"] == target_n]["run_id"].unique()
        if len(run_ids) == 0:
            continue
        run_id = run_ids[0]
        run_req = sub_req[sub_req["run_id"] == run_id].copy()
        run_back = sub_back[sub_back["run_id"] == run_id].copy()
        if run_req.empty:
            continue
        t0 = float(run_req["start_mono"].min())
        run_req["t_rel_s"] = run_req["start_mono"] - t0
        plt.figure(figsize=(8, 5))
        plt.scatter(run_req["t_rel_s"], run_req["latency_ms"], s=8, label="latência")
        plt.xlabel("Tempo relativo (s)")
        plt.ylabel("Latência (ms)")
        plt.title(f"Série temporal de latência. carga {workload}, N={target_n}")
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.savefig(outdir / f"latency_timeseries_{workload}_N{target_n}.png")
        plt.close()

        if not run_back.empty:
            plt.figure(figsize=(8, 5))
            plt.plot(run_back["t_rel_s"], run_back["in_flight"], marker="o", markersize=2)
            plt.xlabel("Tempo relativo (s)")
            plt.ylabel("Requisições pendentes")
            plt.title(f"Backlog. carga {workload}, N={target_n}")
            plt.grid(True, alpha=0.3)
            plt.tight_layout()
            plt.savefig(outdir / f"backlog_{workload}_N{target_n}.png")
            plt.close()



def build_readme(outdir: Path) -> None:
    text = """# EaaS concurrency benchmark

## Requisitos

```bash
python -m pip install -r requirements.txt
```

## Teste rápido sem infraestrutura externa

```bash
python eaas_concurrency_bench.py \
  --transport mock \
  --concurrency 2 \
  --duration 10 \
  --repeats 2 \
  --output-dir results_quicktest
```

## Execução contra a API real

```bash
python eaas_concurrency_bench.py \
  --base-url http://localhost:8081 \
  --endpoint /keys/pop \
  --concurrency 1 2 4 8 16 32 64 \
  --duration 60 \
  --repeats 20 \
  --workloads A B C \
  --output-dir results_real
```

## Observações

1. A carga A usa `size_bits=2048`.
2. A carga B usa `size_bits=256`.
3. A carga C usa mistura 80% de 256 bits e 20% de 2048 bits.
4. Se `size_bits < 2048`, o script contabiliza desperdício assumindo descarte do restante do bloco.
5. O modo `--stop-on-exhaustion` encerra uma repetição após o primeiro HTTP 4xx.

## Saídas

- `raw_requests.csv`: dados brutos por requisição.
- `raw_backlog.csv`: amostras temporais de requisições pendentes.
- `run_summary.csv`: resumo por repetição.
- `aggregate_summary.csv`: agregação por configuração.
- `analysis.txt`: análise textual automática.
- gráficos `.png`.
"""
    (outdir / "README.md").write_text(text, encoding="utf-8")



def build_requirements(outdir: Path) -> None:
    req = """httpx>=0.27
pandas>=2.2
numpy>=1.26
matplotlib>=3.8
"""
    (outdir / "requirements.txt").write_text(req, encoding="utf-8")


async def main_async(args: argparse.Namespace) -> None:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    all_req: List[pd.DataFrame] = []
    all_back: List[pd.DataFrame] = []
    summaries: List[Dict[str, Any]] = []

    for workload in args.workloads:
        for concurrency in args.concurrency:
            for repeat in range(1, args.repeats + 1):
                req_df, backlog_df, summary = await run_single_configuration(
                    base_url=args.base_url,
                    endpoint=args.endpoint,
                    workload=workload,
                    concurrency=concurrency,
                    repeat=repeat,
                    duration_s=args.duration,
                    stop_on_exhaustion=args.stop_on_exhaustion,
                    timeout_s=args.timeout,
                    max_connections=max(args.max_connections, concurrency),
                    transport_mode=args.transport,
                    mock_blocks=args.mock_blocks,
                    seed=args.seed,
                )
                all_req.append(req_df)
                all_back.append(backlog_df)
                summaries.append(summary)
                print(
                    f"[done] workload={workload} N={concurrency} repeat={repeat} "
                    f"success={summary['requests_success']} total={summary['requests_total']} "
                    f"p95={summary['latency_p95_ms']} req/s={summary['throughput_req_s']:.2f}"
                )

    req_df = pd.concat(all_req, ignore_index=True) if all_req else pd.DataFrame()
    back_df = pd.concat(all_back, ignore_index=True) if all_back else pd.DataFrame()
    summary_df = pd.DataFrame(summaries)
    agg_df = aggregate_summaries(summary_df, req_df) if not summary_df.empty else pd.DataFrame()

    req_df.to_csv(output_dir / "raw_requests.csv", index=False)
    back_df.to_csv(output_dir / "raw_backlog.csv", index=False)
    summary_df.to_csv(output_dir / "run_summary.csv", index=False)
    agg_df.to_csv(output_dir / "aggregate_summary.csv", index=False)

    if not agg_df.empty:
        save_plot(
            agg_df,
            x="concurrency",
            ys=[("latency_p50_ms_median", "p50"), ("latency_p95_ms_median", "p95"), ("latency_p99_ms_median", "p99")],
            outpath=output_dir / "latency_vs_concurrency.png",
            title="Latência vs concorrência",
            ylabel="Latência (ms)",
        )
        save_plot(
            agg_df,
            x="concurrency",
            ys=[("throughput_req_s_median", "req/s")],
            outpath=output_dir / "throughput_vs_concurrency.png",
            title="Throughput vs concorrência",
            ylabel="req/s",
        )
        save_plot(
            agg_df,
            x="concurrency",
            ys=[("fairness_jain_median", "Jain")],
            outpath=output_dir / "fairness_vs_concurrency.png",
            title="Fairness vs concorrência",
            ylabel="Índice de Jain",
        )
        save_plot(
            agg_df,
            x="concurrency",
            ys=[("throughput_effective_bits_s_median", "bits/s")],
            outpath=output_dir / "bits_per_s_vs_concurrency.png",
            title="Bits/s efetivos vs concorrência",
            ylabel="bits/s",
        )

    save_time_series(req_df, back_df, output_dir)
    analysis = generate_text_analysis(agg_df, summary_df, req_df)
    (output_dir / "analysis.txt").write_text(analysis + "\n", encoding="utf-8")
    build_readme(output_dir)
    build_requirements(output_dir)

    print("\n===== ANÁLISE AUTOMÁTICA =====")
    print(analysis)
    print(f"\nResultados salvos em: {output_dir}")



def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Benchmark concorrente para API EaaS")
    p.add_argument("--base-url", default="http://localhost:8081")
    p.add_argument("--endpoint", default="/keys/pop")
    p.add_argument("--transport", choices=["network", "mock"], default="network")
    p.add_argument("--mock-blocks", type=int, default=500)
    p.add_argument("--workloads", nargs="+", choices=["A", "B", "C"], default=["A", "B", "C"])
    p.add_argument("--concurrency", nargs="+", type=int, default=DEFAULT_CONCURRENCY)
    p.add_argument("--duration", type=float, default=60.0)
    p.add_argument("--repeats", type=int, default=20)
    p.add_argument("--timeout", type=float, default=10.0)
    p.add_argument("--max-connections", type=int, default=128)
    p.add_argument("--stop-on-exhaustion", action="store_true")
    p.add_argument("--seed", type=int, default=12345)
    p.add_argument("--output-dir", default="results")
    return p.parse_args(argv)



def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
