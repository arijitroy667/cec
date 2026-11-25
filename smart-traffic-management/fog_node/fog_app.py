"""
FOG NODE - Fog Layer (Distributed Intelligence)
Fog Node / Local Traffic Controller for DISTRICT-01 (10 adjacent intersections)

Slide 31: "Fog Layer: Coordinates signal timings dynamically across 10
adjacent intersections to smooth regional traffic flow."
Slide 16: "Traffic data from thousands of intersection sensors is processed by
a district fog node before reaching the central cloud."

Single Fog Node functions (slide 31):
  * Real-Time Traffic Flow Optimization (local)   -> signal_coordination.py
  * Data Aggregation & Filtering                  -> district summary for the Cloud
  * Event Detection (accidents, congestion)       -> reroute advisories
  * V2X Message Processing                        -> V2X advisories in the commands

Message flow:
  Edge  -> Fog  : Raw Sensor Data & Actuation Requests   POST /fog/coordinate
  Fog   -> Edge : Local Control Commands                 (response)
  Fog   -> Cloud: Aggregated Traffic Data & Analytics    POST cloud /cloud/ingest
  Cloud -> Fog  : Central Control Commands               GET  cloud /cloud/central-control-commands
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time

from flask import Flask, jsonify, request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.terminology import (  # noqa: E402
    FOG_LAYER, LAYER_ROLE, DISTRICT_ID, MSG_LOCAL_CONTROL_COMMANDS,
    MSG_AGGREGATED_TRAFFIC_DATA_AND_ANALYTICS, MSG_RAW_SENSOR_DATA_AND_ACTUATION_REQUESTS,
)
from common.transport import LinkProfile, post_json, get_json  # noqa: E402
from fog_node.signal_coordination import coordinate_signal_timings  # noqa: E402

NODE_NAME = os.getenv("NODE_NAME", "fog-node")
PORT = int(os.getenv("FOG_PORT", "6000"))
CLOUD_URL = os.getenv("CLOUD_URL", "http://cloud-node:7000")
FOG_CLOUD_LINK = LinkProfile("Fog <-> Cloud (backhaul)", os.getenv("FOG_CLOUD_RTT_MS", "60"),
                             os.getenv("FOG_CLOUD_BANDWIDTH_MBIT", "200"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [FOG] %(message)s")
log = logging.getLogger("fog")
logging.getLogger("werkzeug").setLevel(logging.WARNING)

app = Flask(__name__)
central_policy = {"policy": None, "fetched_at": 0.0}
policy_lock = threading.Lock()
stats = {"coordination_requests": 0, "cloud_sync_ok": 0, "cloud_sync_failed": 0}

DEFAULT_POLICY = {"policy_version": "fog-default", "min_cycle_s": 40, "max_cycle_s": 150,
                  "lost_time_per_phase_s": 4, "saturation_flow_vph_per_lane": 1800,
                  "lanes_per_approach": 2, "corridor_speed_limit_kmh": 50,
                  "emergency_vehicle_prioritization": True, "accident_reroute_advisory": True}


def get_central_policy():
    """Central Control Commands are cached for 60 s.  If the WAN to the Cloud
    is down the Fog keeps working with its last/default policy (autonomy)."""
    with policy_lock:
        if central_policy["policy"] and time.time() - central_policy["fetched_at"] < 60:
            return central_policy["policy"]
        try:
            msg = get_json(f"{CLOUD_URL}/cloud/central-control-commands", FOG_CLOUD_LINK, timeout=5)
            central_policy.update(policy=msg["policy"], fetched_at=time.time())
            log.info("Central Control Commands received from Cloud (policy %s)", msg["policy"]["policy_version"])
        except Exception as exc:  # noqa: BLE001
            log.warning("Cloud unreachable (%s) - using cached/default policy", exc)
            if not central_policy["policy"]:
                return DEFAULT_POLICY
        return central_policy["policy"]


def sync_to_cloud(summary):
    """Asynchronous Cloud Sync of Aggregated Traffic Data & Analytics (slide 28)."""
    try:
        post_json(f"{CLOUD_URL}/cloud/ingest", summary, FOG_CLOUD_LINK, timeout=10)
        stats["cloud_sync_ok"] += 1
    except Exception as exc:  # noqa: BLE001
        stats["cloud_sync_failed"] += 1
        log.warning("Cloud sync failed: %s", exc)


@app.get("/health")
def health():
    return jsonify({"node": NODE_NAME, "layer": FOG_LAYER, "role": LAYER_ROLE[FOG_LAYER],
                    "district_id": DISTRICT_ID, "status": "UP", "stats": stats,
                    "fog_cloud_link": FOG_CLOUD_LINK.as_dict()})


@app.post("/fog/coordinate")
def coordinate():
    """Raw Sensor Data & Actuation Requests (Edge) -> Local Control Commands."""
    t0 = time.perf_counter()
    req = request.get_json(force=True)
    states = req["intersections"]
    events = req.get("active_events", [])
    policy = get_central_policy()

    plan = coordinate_signal_timings(states, events, policy)
    fog_exec = time.perf_counter() - t0

    # Data Aggregation & Filtering -> district summary for the Cloud (Traffic Data Lake)
    summary = {"message": MSG_AGGREGATED_TRAFFIC_DATA_AND_ANALYTICS, "district_id": DISTRICT_ID,
               "fog_node": NODE_NAME, "task_id": req.get("task_id"),
               "district_flow_vph": round(sum(s.get("flow_vph", 0) for s in states), 1),
               "congestion_levels": {s["intersection_id"]: s.get("congestion_level") for s in states},
               "active_events": events, "common_cycle_s": plan["common_cycle_s"]}
    threading.Thread(target=sync_to_cloud, args=(summary,), daemon=True).start()

    stats["coordination_requests"] += 1
    preempt = sum(1 for c in plan["local_control_commands"] for a in c["actions"]
                  if a["action"] == "EMERGENCY_VEHICLE_PREEMPTION")
    log.info("%s for task %s: %d intersections -> common cycle %ds, %d emergency pre-emptions",
             MSG_RAW_SENSOR_DATA_AND_ACTUATION_REQUESTS, req.get("task_id"), len(states),
             plan["common_cycle_s"], preempt)
    return jsonify({"message": MSG_LOCAL_CONTROL_COMMANDS, "district_id": DISTRICT_ID,
                    "issued_by": NODE_NAME, "layer": FOG_LAYER,
                    "policy_version": policy.get("policy_version"),
                    "fog_execution_s": fog_exec, **plan})


if __name__ == "__main__":
    log.info("%s up - %s - %s - port %d", NODE_NAME, FOG_LAYER, DISTRICT_ID, PORT)
    app.run(host="0.0.0.0", port=PORT, threaded=True)
