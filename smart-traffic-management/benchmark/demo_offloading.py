"""
Demonstration of task transfer and communication between the containers.

Runs one task with each strategy (Local Execution, Full Offloading, Partial
Offloading) plus the adaptive Offloading Decision Engine, and prints WHEN,
WHY and HOW the task was offloaded, where every sub-task ran, how many bytes
crossed the Edge->Cloud link, the timings and the Fog Node's Local Control
Commands.

Usage:  python3 benchmark/demo_offloading.py --workload large
"""
from __future__ import annotations

import argparse
import json

import requests

LINE = "=" * 78


def show(rec):
    print(LINE)
    title = rec["strategy_title"].upper()
    if rec["requested_strategy"] == "auto":
        title = "ADAPTIVE (OFFLOADING DECISION ENGINE) -> " + title
    print(f" {title}   task {rec['task_id']}   ({rec['num_readings']} raw sensor readings)")
    print(LINE)
    if rec["requested_strategy"] == "auto":
        d = rec["decision_engine"]
        print(" Offloading Decision Engine estimates (s):", d["estimated_response_time_s"])
        print(" WHY :", d["reason"])
    print(" WHERE each sub-task ran:")
    for k, v in rec["task_placement"].items():
        print(f"   {k:36s} -> {v}")
    tr = rec.get("transfer") or {}
    print(" HOW (Edge -> Cloud transfer, JSON over HTTP):")
    print(f"   bytes sent to Cloud      : {rec['bytes_to_cloud']:>12,d}")
    print(f"   bytes received from Cloud: {rec['bytes_from_cloud']:>12,d}")
    if tr:
        print(f"   serialization {tr['serialization_s']:.3f}s | HTTP round trip {tr['http_round_trip_s']:.3f}s"
              f" | emulated WAN delay {tr['emulated_network_delay_s']:.3f}s")
    print(" TIMES:")
    print(f"   Edge execution           : {rec['edge_execution_s']:.4f} s")
    print(f"   Cloud execution          : {rec['cloud_execution_s']:.4f} s")
    print(f"   Task execution time      : {rec['task_execution_time_s']:.4f} s")
    print(f"   End-to-end response time : {rec['end_to_end_response_time_s']:.4f} s")
    fog = rec.get("fog") or {}
    if fog and "error" not in fog:
        print(f" FOG NODE: {fog['message']} for {fog['commands']} intersections, common cycle "
              f"{fog['common_cycle_s']} s, fog round trip {fog['fog_round_trip_s']:.3f} s")
        print("   sample command:", json.dumps(fog["sample_command"]))
    for a in rec["instant_actuation"]["actions"]:
        print(f" INSTANT ACTUATION at Edge: {a['action']} {a['intersection_id']} approach {a['approach']}")
    print(" Top bottleneck:", rec["result_summary"]["top_bottlenecks"][0]["intersection_id"],
          "| events detected:", rec["result_summary"]["events_detected"],
          "| result digest:", rec["result_digest"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--edge", default="http://localhost:8000")
    ap.add_argument("--workload", default="large", choices=["small", "medium", "large"])
    a = ap.parse_args()
    digests = {}
    for st in ("local", "full", "partial", "auto"):
        rec = requests.post(f"{a.edge}/edge/run-task", json={"strategy": st, "workload": a.workload},
                            timeout=600).json()
        show(rec)
        digests[st] = rec["result_digest"]
    print(LINE)
    same = len(set(digests.values())) == 1
    print(" Result digests:", digests, "->", "IDENTICAL results for every strategy" if same else "DIFFERENT")
    print(LINE)


if __name__ == "__main__":
    main()
