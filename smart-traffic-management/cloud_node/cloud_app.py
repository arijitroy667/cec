"""
CLOUD NODE - Cloud Layer (Centralized Intelligence)
Central Traffic Management System (TMS)

Slide 31: "Cloud Layer: Analyzes city-wide historical traffic patterns for
long-term urban planning."

Responsibilities of this container
  * Receives offloaded computational tasks from the Edge Node
      POST /cloud/offload/full     - Full Offloading: complete task (T1-T4)
      POST /cloud/offload/partial  - Partial Offloading: Cloud Layer tasks (T3-T4)
  * Long-term data storage (Traffic Data Lake) of the Aggregated Traffic Data &
    Analytics pushed by the Fog Node        POST /cloud/ingest
  * Global policy & rule management -> Central Control Commands for the Fog
                                           GET  /cloud/central-control-commands
  * Exposes compute capacity / health        GET  /health

The Cloud has far more compute than the Edge (slide 23: "Virtually
Unlimited" vs "Constrained / Limited").  It processes the 10 intersections
in parallel with a pool of worker processes (CLOUD_WORKERS).
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from concurrent.futures import ProcessPoolExecutor

from flask import Flask, jsonify, request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.terminology import (  # noqa: E402
    CLOUD_LAYER, LAYER_ROLE, DISTRICT_ID, MSG_CENTRAL_CONTROL_COMMANDS,
    MSG_AGGREGATED_TRAFFIC_DATA_AND_ANALYTICS,
)
from common import traffic_tasks as T  # noqa: E402

NODE_NAME = os.getenv("NODE_NAME", "cloud-node")
PORT = int(os.getenv("CLOUD_PORT", "7000"))
CLOUD_WORKERS = int(os.getenv("CLOUD_WORKERS", "2"))
DATA_LAKE_PATH = os.getenv("DATA_LAKE_PATH", "/data/traffic_data_lake.jsonl")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [CLOUD] %(message)s")
log = logging.getLogger("cloud")
logging.getLogger("werkzeug").setLevel(logging.WARNING)

app = Flask(__name__)
pool: ProcessPoolExecutor | None = None
lake_lock = threading.Lock()
stats = {"full_offloading_tasks": 0, "partial_offloading_tasks": 0, "fog_ingest_messages": 0}

# Global Policy & Rule Management (slide 31, cloud box)
CENTRAL_POLICY = {
    "policy_version": "2026.11-v3",
    "min_cycle_s": 40,
    "max_cycle_s": 150,
    "lost_time_per_phase_s": 4,
    "saturation_flow_vph_per_lane": 1800,
    "lanes_per_approach": 2,
    "corridor_speed_limit_kmh": 50,
    "emergency_vehicle_prioritization": True,
    "accident_reroute_advisory": True,
}


# ---------------------------------------------------------------------------
# Worker functions (run in the process pool)
# ---------------------------------------------------------------------------
def _full_task_for_intersection(args):
    iid, readings = args
    agg, t12 = T.run_edge_tasks_for_intersection(iid, readings)
    pattern, t3 = T.run_cloud_tasks_for_intersection(agg)
    return iid, agg, pattern, {**t12, **t3}


def _cloud_tasks_for_intersection(agg):
    pattern, t3 = T.run_cloud_tasks_for_intersection(agg)
    return agg["intersection_id"], pattern, t3


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return jsonify({"node": NODE_NAME, "layer": CLOUD_LAYER, "role": LAYER_ROLE[CLOUD_LAYER],
                    "status": "UP", "workers": CLOUD_WORKERS, "stats": stats,
                    "policy_version": CENTRAL_POLICY["policy_version"]})


@app.post("/cloud/offload/full")
def full_offloading():
    """Full Offloading: the Edge transferred the complete computational task
    (all Raw Sensor Telemetry).  The Cloud executes T1-T4 and returns the result."""
    t_req = time.perf_counter()
    body = request.get_json(force=True)
    t_parsed = time.perf_counter()
    task_id = body.get("task_id")
    raw = body["raw_sensor_telemetry"]
    log.info("Full Offloading task %s received: %d raw sensor readings (%.2f MB)",
             task_id, len(raw), (request.content_length or 0) / 1e6)

    t0 = time.perf_counter()
    parts, unroutable = T.partition_by_intersection(raw)
    aggregated, patterns, timings = {}, {}, {}
    for iid, agg, pattern, tim in pool.map(_full_task_for_intersection, parts.items()):
        aggregated[iid], patterns[iid] = agg, pattern
        T.add_timings(timings, tim)
    t4 = time.perf_counter()
    citywide = T.cloud_citywide_traffic_analytics(aggregated, patterns)
    timings["T4_citywide_traffic_analytics"] = time.perf_counter() - t4
    result = T.assemble_result(aggregated, patterns, citywide, unroutable)
    exec_s = time.perf_counter() - t0

    stats["full_offloading_tasks"] += 1
    log.info("Full Offloading task %s done: cloud execution %.3f s with %d workers",
             task_id, exec_s, CLOUD_WORKERS)
    return jsonify({"task_id": task_id, "executed_by": NODE_NAME, "layer": CLOUD_LAYER,
                    "result": result,
                    "cloud_execution_s": exec_s,
                    "cloud_request_parse_s": t_parsed - t_req,
                    "subtask_cpu_s": timings,
                    "workers": CLOUD_WORKERS})


@app.post("/cloud/offload/partial")
def partial_offloading():
    """Partial Offloading: the Edge already executed the Edge Layer tasks (T1, T2)
    and transferred only the Aggregated Traffic Data.  The Cloud executes the
    Cloud Layer tasks (T3 historical pattern analysis, T4 city-wide analytics)."""
    t_req = time.perf_counter()
    body = request.get_json(force=True)
    t_parsed = time.perf_counter()
    task_id = body.get("task_id")
    aggregated = body["aggregated_traffic_data"]
    log.info("Partial Offloading task %s received: aggregated traffic data of %d intersections (%.2f MB)",
             task_id, len(aggregated), (request.content_length or 0) / 1e6)

    t0 = time.perf_counter()
    patterns, timings = {}, {}
    for iid, pattern, tim in pool.map(_cloud_tasks_for_intersection, aggregated.values()):
        patterns[iid] = pattern
        T.add_timings(timings, tim)
    t4 = time.perf_counter()
    citywide = T.cloud_citywide_traffic_analytics(aggregated, patterns)
    timings["T4_citywide_traffic_analytics"] = time.perf_counter() - t4
    exec_s = time.perf_counter() - t0

    stats["partial_offloading_tasks"] += 1
    log.info("Partial Offloading task %s done: cloud execution %.3f s", task_id, exec_s)
    return jsonify({"task_id": task_id, "executed_by": NODE_NAME, "layer": CLOUD_LAYER,
                    "historical_traffic_patterns": patterns,
                    "citywide_traffic_analytics": citywide,
                    "cloud_execution_s": exec_s,
                    "cloud_request_parse_s": t_parsed - t_req,
                    "subtask_cpu_s": timings,
                    "workers": CLOUD_WORKERS})


@app.post("/cloud/ingest")
def ingest_from_fog():
    """Aggregated Traffic Data & Analytics from the Fog Node -> Traffic Data Lake."""
    msg = request.get_json(force=True)
    msg["ingested_at"] = time.time()
    with lake_lock:
        os.makedirs(os.path.dirname(DATA_LAKE_PATH), exist_ok=True)
        with open(DATA_LAKE_PATH, "a") as f:
            f.write(json.dumps(msg) + "\n")
    stats["fog_ingest_messages"] += 1
    return jsonify({"stored": True, "message": MSG_AGGREGATED_TRAFFIC_DATA_AND_ANALYTICS})


@app.get("/cloud/central-control-commands")
def central_control_commands():
    """Central Control Commands (global policy) consumed by the Fog Node."""
    return jsonify({"message": MSG_CENTRAL_CONTROL_COMMANDS, "district_id": DISTRICT_ID,
                    "issued_by": NODE_NAME, "policy": CENTRAL_POLICY})


@app.get("/cloud/data-lake/summary")
def data_lake_summary():
    n, last = 0, None
    if os.path.exists(DATA_LAKE_PATH):
        with open(DATA_LAKE_PATH) as f:
            for line in f:
                n += 1
                last = line
    return jsonify({"records": n, "last_record": json.loads(last) if last else None})


if __name__ == "__main__":
    pool = ProcessPoolExecutor(max_workers=CLOUD_WORKERS)
    list(pool.map(abs, range(CLOUD_WORKERS)))        # warm up the worker processes
    log.info("%s up - %s - %d worker processes - port %d", NODE_NAME, CLOUD_LAYER, CLOUD_WORKERS, PORT)
    app.run(host="0.0.0.0", port=PORT, threaded=True)
