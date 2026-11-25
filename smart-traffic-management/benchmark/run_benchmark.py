"""
Performance Evaluation of the three computational offloading strategies.

Runs on the Docker host (the Ubuntu VM) and drives the Edge Node REST API.

Experiment 1 - workload size:    small / medium / large  x  Local / Full / Partial
Experiment 2 - network:          WAN RTT and bandwidth sweep (medium workload)
Experiment 3 - decision engine:  does the Offloading Decision Engine pick the
                                 strategy that was actually fastest?

Metrics (Python time module, measured inside the containers):
  task execution time      = compute time spent on the task (Edge + Cloud)
  end-to-end response time = from 'raw sensor telemetry ready at the Edge'
                             to 'processed result available at the Edge'
Resource usage: Docker Stats (CPU %, memory) per container and phase.

Usage:
  python3 benchmark/run_benchmark.py                       # full evaluation
  python3 benchmark/run_benchmark.py --reps 3 --quick      # shorter run
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from docker_stats_monitor import DockerStatsMonitor  # noqa: E402

STRATEGIES = ("local", "full", "partial")
TITLES = {"local": "Local Execution", "full": "Full Offloading", "partial": "Partial Offloading"}
WORKLOADS = ("small", "medium", "large")


def run(edge, strategy, workload, seed=42):
    r = requests.post(f"{edge}/edge/run-task",
                      json={"strategy": strategy, "workload": workload, "seed": seed, "include_fog": True},
                      timeout=600)
    r.raise_for_status()
    return r.json()


def set_network(edge, rtt, bw):
    requests.post(f"{edge}/edge/network", json={"cloud_rtt_ms": rtt, "cloud_bandwidth_mbit": bw},
                  timeout=30).raise_for_status()


def row_of(exp, rec, rep, rtt, bw):
    tr = rec.get("transfer") or {}
    fog = rec.get("fog") or {}
    return {"experiment": exp, "workload": rec["workload"], "num_readings": rec["num_readings"],
            "strategy": rec["strategy"], "rep": rep, "rtt_ms": rtt, "bandwidth_mbit": bw,
            "edge_execution_s": rec["edge_execution_s"], "cloud_execution_s": rec["cloud_execution_s"],
            "task_execution_time_s": rec["task_execution_time_s"],
            "end_to_end_response_time_s": rec["end_to_end_response_time_s"],
            "communication_overhead_s": rec["communication_overhead_s"],
            "bytes_to_cloud": rec["bytes_to_cloud"], "bytes_from_cloud": rec["bytes_from_cloud"],
            "emulated_network_delay_s": round(tr.get("emulated_network_delay_s", 0.0), 4),
            "fog_round_trip_s": round(fog.get("fog_round_trip_s", 0.0) or 0.0, 4),
            "result_digest": rec["result_digest"],
            "recommended_strategy": rec["decision_engine"]["recommended_strategy"]}


def summarize(rows, keys):
    groups = {}
    for r in rows:
        groups.setdefault(tuple(r[k] for k in keys), []).append(r)
    out = []
    for k, rs in groups.items():
        e2e = [r["end_to_end_response_time_s"] for r in rs]
        ex = [r["task_execution_time_s"] for r in rs]
        d = dict(zip(keys, k))
        d.update({"runs": len(rs),
                  "task_execution_mean_s": round(statistics.fmean(ex), 4),
                  "task_execution_sd_s": round(statistics.stdev(ex), 4) if len(ex) > 1 else 0.0,
                  "edge_execution_mean_s": round(statistics.fmean(r["edge_execution_s"] for r in rs), 4),
                  "cloud_execution_mean_s": round(statistics.fmean(r["cloud_execution_s"] for r in rs), 4),
                  "e2e_mean_s": round(statistics.fmean(e2e), 4),
                  "e2e_sd_s": round(statistics.stdev(e2e), 4) if len(e2e) > 1 else 0.0,
                  "e2e_min_s": round(min(e2e), 4), "e2e_max_s": round(max(e2e), 4),
                  "communication_mean_s": round(statistics.fmean(r["communication_overhead_s"] for r in rs), 4),
                  "mb_to_cloud": round(statistics.fmean(r["bytes_to_cloud"] for r in rs) / 1e6, 3),
                  "fog_round_trip_mean_s": round(statistics.fmean(r["fog_round_trip_s"] for r in rs), 4),
                  "digests": sorted({r["result_digest"] for r in rs})})
        out.append(d)
    return out


def write_csv(path, rows):
    if not rows:
        return
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow({k: (";".join(v) if isinstance(v, list) else v) for k, v in r.items()})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--edge", default="http://localhost:8000")
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--out", default="results")
    ap.add_argument("--rtt", type=float, default=100.0, help="default Edge-Cloud RTT (ms)")
    ap.add_argument("--bw", type=float, default=50.0, help="default Edge-Cloud bandwidth (Mbit/s)")
    ap.add_argument("--quick", action="store_true", help="skip the network sweep")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    print("Edge Node:", requests.get(f"{a.edge}/health", timeout=10).json())
    stats = DockerStatsMonitor().start()
    rows = []

    # ---------------- Experiment 1: workload size
    set_network(a.edge, a.rtt, a.bw)
    for wl in WORKLOADS:
        for st in STRATEGIES:
            stats.phase = f"{wl}/{st}"
            run(a.edge, st, wl)                        # warm-up run (not recorded)
            for rep in range(1, a.reps + 1):
                rec = run(a.edge, st, wl)
                rows.append(row_of("workload", rec, rep, a.rtt, a.bw))
                print(f"[workload] {wl:6s} {TITLES[st]:18s} rep {rep}: exec {rec['task_execution_time_s']:.3f}s "
                      f"e2e {rec['end_to_end_response_time_s']:.3f}s  -> {rec['bytes_to_cloud']/1e6:.2f} MB to cloud")
    stats.phase = "idle"

    # ---------------- Experiment 2: network sweep (medium workload)
    sweep = [] if a.quick else [(20, 50), (100, 50), (300, 50), (100, 10), (100, 200)]
    for rtt, bw in sweep:
        set_network(a.edge, rtt, bw)
        for st in STRATEGIES:
            stats.phase = f"net/{rtt}ms-{bw}mbit/{st}"
            for rep in range(1, max(2, a.reps - 2) + 1):
                rec = run(a.edge, st, "medium")
                rows.append(row_of("network", rec, rep, rtt, bw))
                print(f"[network] RTT {rtt:>3}ms {bw:>3}Mbit {TITLES[st]:18s} rep {rep}: "
                      f"e2e {rec['end_to_end_response_time_s']:.3f}s")
    set_network(a.edge, a.rtt, a.bw)
    stats.phase = "idle"
    stats.stop()

    # ---------------- Summaries
    s_work = summarize([r for r in rows if r["experiment"] == "workload"], ["workload", "strategy"])
    s_net = summarize([r for r in rows if r["experiment"] == "network"], ["rtt_ms", "bandwidth_mbit", "strategy"])

    # Experiment 3: decision engine check against the measured best
    decisions = []
    for grp_keys, summ, exp in ((("workload",), s_work, "workload"), (("rtt_ms", "bandwidth_mbit"), s_net, "network")):
        cfgs = {}
        for s in summ:
            cfgs.setdefault(tuple(s[k] for k in grp_keys), []).append(s)
        for cfg, ss in cfgs.items():
            best = min(ss, key=lambda x: x["e2e_mean_s"])["strategy"]
            recs = [r["recommended_strategy"] for r in rows if r["experiment"] == exp
                    and tuple(r[k] for k in grp_keys) == cfg]
            rec = max(set(recs), key=recs.count)
            decisions.append({"experiment": exp, "config": "/".join(map(str, cfg)),
                              "measured_best": best, "engine_recommendation": rec, "match": best == rec})

    write_csv(os.path.join(a.out, "raw_runs.csv"), rows)
    write_csv(os.path.join(a.out, "summary_workload.csv"), s_work)
    write_csv(os.path.join(a.out, "summary_network.csv"), s_net)
    write_csv(os.path.join(a.out, "decision_engine_check.csv"), decisions)
    stats.write_csv(os.path.join(a.out, "docker_stats_samples.csv"))
    write_csv(os.path.join(a.out, "docker_stats_summary.csv"), stats.summary())
    with open(os.path.join(a.out, "summary.json"), "w") as f:
        json.dump({"created": time.strftime("%Y-%m-%d %H:%M:%S"), "reps": a.reps,
                   "default_network": {"rtt_ms": a.rtt, "bandwidth_mbit": a.bw},
                   "workload": s_work, "network": s_net, "decision_engine": decisions,
                   "docker_stats": stats.summary()}, f, indent=2)

    print("\n=== Workload experiment (RTT %.0f ms, %.0f Mbit/s) ===" % (a.rtt, a.bw))
    print(f"{'workload':8s} {'strategy':20s} {'exec (s)':>10s} {'e2e (s)':>10s} {'MB->cloud':>10s} digest")
    for s in s_work:
        print(f"{s['workload']:8s} {TITLES[s['strategy']]:20s} {s['task_execution_mean_s']:10.3f} "
              f"{s['e2e_mean_s']:10.3f} {s['mb_to_cloud']:10.2f} {','.join(s['digests'])}")
    print("\nDecision engine:", sum(d["match"] for d in decisions), "/", len(decisions), "configs matched")
    print("Results written to", os.path.abspath(a.out))


if __name__ == "__main__":
    main()
