"""
EDGE NODE - Edge Layer (Data Acquisition & Actuation)
Ultra-low latency, immediate response

Slide 31: "Edge Layer: Smart cameras perform intersection video object
detection locally, identifying vehicle counts and congestion in real time."

This container is the Roadside-Unit gateway of DISTRICT-01.  It
  1. Senses   - collects Raw Sensor Telemetry from the Device Layer
                (smart cameras, induction loops, radar of 10 adjacent intersections)
  2. Decides  - where the computational task runs (Local / Full / Partial
                Offloading, or the Offloading Decision Engine for 'auto')
  3. Executes - its share of the task, transfers the rest to the Cloud Node
                (JSON over HTTP) and receives the processed result
  4. Acts     - sends Raw Sensor Data & Actuation Requests to the Fog Node,
                receives Local Control Commands and performs Instant Actuation
                (traffic signal control, emergency vehicle prioritization)

REST API (Flask)
  GET  /health
  POST /edge/run-task        {"strategy": "local|full|partial|auto", "workload": "small|medium|large",
                              "num_readings": int, "seed": int, "include_fog": bool, "return_result": bool}
  GET  /edge/decision?workload=medium      Offloading Decision Engine estimate only
  GET  /edge/network                       current Edge<->Cloud / Edge<->Fog network profiles
  POST /edge/network         {"cloud_rtt_ms": 100, "cloud_bandwidth_mbit": 50, ...}
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
    EDGE_LAYER, LAYER_ROLE, DISTRICT_ID, WORKLOADS, STRATEGY_TITLE,
    LOCAL_EXECUTION, FULL_OFFLOADING, PARTIAL_OFFLOADING, AUTO_OFFLOADING,
    MSG_RAW_SENSOR_DATA_AND_ACTUATION_REQUESTS,
    STAGE_SENSE, STAGE_FILTER, STAGE_PROCESS, STAGE_ANALYZE, STAGE_ACT,
)
from common import traffic_tasks as T  # noqa: E402
from common.transport import LinkProfile, post_json, get_json  # noqa: E402
from edge_node.device_layer import generate_raw_sensor_telemetry  # noqa: E402
from edge_node import offloading as OFF  # noqa: E402
from edge_node.network_emulation import apply_netem  # noqa: E402

NODE_NAME = os.getenv("NODE_NAME", "edge-node")
PORT = int(os.getenv("EDGE_PORT", "5000"))
CLOUD_URL = os.getenv("CLOUD_URL", "http://cloud-node:7000")
FOG_URL = os.getenv("FOG_URL", "http://fog-node:6000")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [EDGE] %(message)s")
log = logging.getLogger("edge")
logging.getLogger("werkzeug").setLevel(logging.WARNING)

# Network profiles (slide 23: Edge->Fog "few network hops", Edge->Cloud "multiple internet hops")
CLOUD_LINK = LinkProfile("Edge <-> Cloud (WAN)", os.getenv("EDGE_CLOUD_RTT_MS", "100"),
                         os.getenv("EDGE_CLOUD_BANDWIDTH_MBIT", "50"))
FOG_LINK = LinkProfile("Edge <-> Fog (LAN/MAN)", os.getenv("EDGE_FOG_RTT_MS", "10"),
                       os.getenv("EDGE_FOG_BANDWIDTH_MBIT", "100"))

app = Flask(__name__)
engine = OFF.OffloadingDecisionEngine(CLOUD_LINK)
task_lock = threading.Lock()          # the Edge Node executes one task at a time (constrained device)
history = []


def configure_kernel_emulation():
    if CLOUD_LINK.mode == "tc":
        cloud_host = CLOUD_URL.split("//")[1].split(":")[0]
        fog_host = FOG_URL.split("//")[1].split(":")[0]
        for host, link in ((cloud_host, CLOUD_LINK), (fog_host, FOG_LINK)):
            st = apply_netem(host, link.rtt_ms, link.bandwidth_mbit)
            if not st["applied"]:
                link.mode = "app"


# ---------------------------------------------------------------------------
# Act: Fog coordination + Instant Actuation
# ---------------------------------------------------------------------------
def actuation_request(result):
    states, active = [], []
    for iid, rt in result["realtime_monitoring"].items():
        cs = rt["current_state"]
        states.append(cs)
        start = cs.get("start", 0)
        active += [e for e in rt["events"] if e.get("at", 0) >= start]
    return states, active


def instant_actuation(states):
    """Edge-local reaction without waiting for any remote node
    (slide 30: 'Local Actuation: Triggers relays without waiting for cloud packets')."""
    t0 = time.perf_counter()
    actions = []
    for st in states:
        if st.get("emergency_vehicle_approach"):
            actions.append({"intersection_id": st["intersection_id"],
                            "action": "EMERGENCY_VEHICLE_PREEMPTION",
                            "approach": st["emergency_vehicle_approach"],
                            "issued_by": NODE_NAME})
            log.info("INSTANT ACTUATION (traffic signal control): emergency vehicle at %s approach %s -> green",
                     st["intersection_id"], st["emergency_vehicle_approach"])
    return actions, time.perf_counter() - t0


def fog_coordination(task_id, states, active):
    payload = {"task_id": task_id, "message": MSG_RAW_SENSOR_DATA_AND_ACTUATION_REQUESTS,
               "district_id": DISTRICT_ID, "intersections": states, "active_events": active}
    resp, net = post_json(f"{FOG_URL}/fog/coordinate", payload, FOG_LINK, timeout=30)
    for cmd in resp["local_control_commands"]:
        log.debug("apply %s", cmd)
    return {"message": resp["message"], "fog_round_trip_s": net["total_s"],
            "fog_execution_s": resp["fog_execution_s"], "common_cycle_s": resp["common_cycle_s"],
            "progression_speed_kmh": resp["progression_speed_kmh"],
            "policy_version": resp.get("policy_version"),
            "local_control_commands": resp["local_control_commands"]}


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return jsonify({"node": NODE_NAME, "layer": EDGE_LAYER, "role": LAYER_ROLE[EDGE_LAYER],
                    "district_id": DISTRICT_ID, "status": "UP", "tasks_executed": len(history),
                    "links": [CLOUD_LINK.as_dict(), FOG_LINK.as_dict()]})


@app.get("/edge/network")
def get_network():
    return jsonify({"links": [CLOUD_LINK.as_dict(), FOG_LINK.as_dict()]})


@app.post("/edge/network")
def set_network():
    b = request.get_json(force=True)
    CLOUD_LINK.rtt_ms = float(b.get("cloud_rtt_ms", CLOUD_LINK.rtt_ms))
    CLOUD_LINK.bandwidth_mbit = float(b.get("cloud_bandwidth_mbit", CLOUD_LINK.bandwidth_mbit))
    FOG_LINK.rtt_ms = float(b.get("fog_rtt_ms", FOG_LINK.rtt_ms))
    FOG_LINK.bandwidth_mbit = float(b.get("fog_bandwidth_mbit", FOG_LINK.bandwidth_mbit))
    configure_kernel_emulation()
    log.info("Network profile updated: %s | %s", CLOUD_LINK.as_dict(), FOG_LINK.as_dict())
    return jsonify({"links": [CLOUD_LINK.as_dict(), FOG_LINK.as_dict()]})


def _num_readings(src):
    if src.get("num_readings"):
        return int(src["num_readings"])
    return WORKLOADS[src.get("workload", "medium")]


@app.get("/edge/decision")
def decision():
    return jsonify(engine.decide(_num_readings(request.args)))


@app.post("/edge/run-task")
def run_task():
    b = request.get_json(force=True) if request.data else {}
    strategy = b.get("strategy", LOCAL_EXECUTION)
    n = _num_readings(b)
    seed = int(b.get("seed", 42))
    include_fog = bool(b.get("include_fog", True))
    with task_lock:
        return jsonify(execute(strategy, n, seed, include_fog, bool(b.get("return_result", False)),
                               b.get("workload")))


def execute(strategy, n, seed, include_fog=True, return_result=False, workload=None):
    task_id = OFF.new_task_id()

    # 1. Acquisition (Sense) - Device Layer -> Edge Layer
    t_s = time.perf_counter()
    raw = generate_raw_sensor_telemetry(n, seed)
    sensing_s = time.perf_counter() - t_s
    log.info("[%s] %s: %d raw sensor readings from 10 adjacent intersections", task_id, STAGE_SENSE, len(raw))

    # 2. Decide WHEN / WHY to offload
    decision_info = engine.decide(len(raw))
    requested = strategy
    if strategy == AUTO_OFFLOADING:
        strategy = decision_info["recommended_strategy"]
        log.info("[%s] Offloading Decision Engine -> %s : %s", task_id, STRATEGY_TITLE[strategy],
                 decision_info["reason"])

    # 3. Execute the computational task - END-TO-END RESPONSE TIME starts here
    fallback = None
    t0 = time.perf_counter()
    try:
        if strategy == LOCAL_EXECUTION:
            result, m, _ = OFF.local_execution(raw)
        elif strategy == FULL_OFFLOADING:
            log.info("[%s] Full Offloading: transferring complete task to %s", task_id, CLOUD_URL)
            result, m, _ = OFF.full_offloading(raw, CLOUD_URL, CLOUD_LINK, task_id)
        elif strategy == PARTIAL_OFFLOADING:
            log.info("[%s] Partial Offloading: %s + %s at Edge, %s at Cloud", task_id,
                     STAGE_FILTER, STAGE_PROCESS, STAGE_ANALYZE)
            result, m, _ = OFF.partial_offloading(raw, CLOUD_URL, CLOUD_LINK, task_id)
        else:
            return {"error": f"unknown strategy {strategy}"}
    except Exception as exc:  # noqa: BLE001 - Offline Autonomy (slide 22)
        if strategy == LOCAL_EXECUTION:
            raise
        log.warning("[%s] Cloud unreachable (%s) -> Local Execution (Offline Autonomy)", task_id, exc)
        fallback = f"{STRATEGY_TITLE[strategy]} failed ({exc.__class__.__name__}); executed locally"
        strategy = LOCAL_EXECUTION
        result, m, _ = OFF.local_execution(raw)
    e2e = time.perf_counter() - t0
    # ------------------------------------------------------------------ result is at the Edge

    task_exec = m["edge_execution_s"] + m["cloud_execution_s"]
    engine.observe(strategy, len(raw), m)

    # 5. Execution (Act) - instant actuation at the Edge + Fog coordination
    states, active = actuation_request(result)
    instant_actions, instant_s = instant_actuation(states)
    fog = None
    if include_fog:
        try:
            fog = fog_coordination(task_id, states, active)
        except Exception as exc:  # noqa: BLE001
            fog = {"error": str(exc)}

    digest = T.result_digest(result)
    record = {
        "task_id": task_id,
        "node": NODE_NAME,
        "requested_strategy": requested,
        "strategy": strategy,
        "strategy_title": STRATEGY_TITLE[strategy],
        "fallback": fallback,
        "workload": workload,
        "num_readings": len(raw),
        "raw_payload_mb": round(len(raw) * engine.raw_bytes_per_reading / 1e6, 3),
        "task_placement": OFF.PLACEMENT[strategy] | {"T5_signal_timing_coordination": "Fog Node"},
        "pipeline": [STAGE_SENSE, STAGE_FILTER, STAGE_PROCESS, STAGE_ANALYZE, STAGE_ACT],
        "sensing_s": round(sensing_s, 4),
        "edge_execution_s": round(m["edge_execution_s"], 4),
        "cloud_execution_s": round(m["cloud_execution_s"], 4),
        "task_execution_time_s": round(task_exec, 4),
        "end_to_end_response_time_s": round(e2e, 4),
        "communication_overhead_s": round(max(0.0, e2e - task_exec), 4),
        "bytes_to_cloud": m["bytes_to_cloud"],
        "bytes_from_cloud": m["bytes_from_cloud"],
        "transfer": m.get("transfer"),
        "subtask_times_s": {k: round(v, 4) for k, v in m["subtask_times_s"].items()},
        "network": {"cloud": CLOUD_LINK.as_dict(), "fog": FOG_LINK.as_dict()},
        "decision_engine": decision_info,
        "instant_actuation": {"actions": instant_actions, "latency_s": round(instant_s, 6)},
        "fog": None if fog is None else {k: v for k, v in fog.items() if k != "local_control_commands"}
               | {"commands": len(fog.get("local_control_commands", [])),
                  "sample_command": (fog.get("local_control_commands") or [None])[0]},
        "result_digest": digest,
        "result_summary": {
            "data_collection": result["data_collection_summary"],
            "top_bottlenecks": result["citywide_traffic_analytics"]["bottleneck_ranking"][:3],
            "corridor_peak_flow_vph": result["citywide_traffic_analytics"]["corridor_peak_flow_vph"],
            "events_detected": sum(len(v["events"]) for v in result["realtime_monitoring"].values()),
        },
    }
    if return_result:
        record["result"] = result
    history.append({k: record[k] for k in ("task_id", "strategy", "num_readings",
                                           "task_execution_time_s", "end_to_end_response_time_s")})
    log.info("[%s] %s done | task execution %.3f s | end-to-end %.3f s | digest %s",
             task_id, STRATEGY_TITLE[strategy], task_exec, e2e, digest)
    return record


def warm_up():
    """Calibrate the Offloading Decision Engine with one small local run."""
    raw = generate_raw_sensor_telemetry(WORKLOADS["small"], 1)
    _, m, _ = OFF.local_execution(raw)
    engine.observe(LOCAL_EXECUTION, len(raw), m)
    try:
        get_json(f"{CLOUD_URL}/health", timeout=5)
        log.info("Cloud Node reachable at %s", CLOUD_URL)
    except Exception:  # noqa: BLE001
        log.warning("Cloud Node not reachable yet at %s", CLOUD_URL)


if __name__ == "__main__":
    configure_kernel_emulation()
    warm_up()
    log.info("%s up - %s - %s - port %d - links: %s | %s", NODE_NAME, EDGE_LAYER, DISTRICT_ID, PORT,
             CLOUD_LINK.as_dict(), FOG_LINK.as_dict())
    app.run(host="0.0.0.0", port=PORT, threaded=True)
