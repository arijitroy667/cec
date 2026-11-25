"""
Computational offloading strategies of the Edge Node + Offloading Decision Engine.

  Local Execution (No Offloading)
      The complete task (T1, T2, T3, T4) runs inside the Edge container.
      Nothing is sent to the Cloud.

  Full Offloading
      The Edge transfers the complete task - all Raw Sensor Telemetry - to the
      Cloud container (JSON over HTTP).  The Cloud executes T1-T4 and returns
      the processed result.

  Partial Offloading
      The task is divided along the Edge Analytics Pipeline (slide 28):
        Edge Node  executes the Edge Layer tasks   T1 Sense/Filter, T2 Process
        Cloud Node executes the Cloud Layer tasks  T3, T4 Analyze
      Only the Aggregated Traffic Data (about 7 % of the raw bytes) crosses
      the WAN - slide 27 "Edge-Centric Processing Pipeline".

WHEN / WHY / HOW the Edge offloads is decided by OffloadingDecisionEngine:
an analytical cost model (no ML) that estimates the end-to-end response time
of each strategy from
    N            number of raw sensor readings in the task
    c_edge, c_cloud   measured per-reading compute cost on each node
    RTT, bandwidth    Edge <-> Cloud network profile
    payload bytes     raw (full) vs aggregated (partial)
and picks the cheapest one.  Its costs are re-calibrated after every run
(exponential moving average) so the decision follows the real system.
"""
from __future__ import annotations

import math
import time
import uuid

from common import traffic_tasks as T
from common.terminology import (
    LOCAL_EXECUTION, FULL_OFFLOADING, PARTIAL_OFFLOADING, STRATEGY_TITLE,
)
from common.transport import post_json

EDGE = "Edge Node"
CLOUD = "Cloud Node"

PLACEMENT = {
    LOCAL_EXECUTION: {"T1_data_collection_and_filtering": EDGE, "T2_realtime_monitoring": EDGE,
                      "T3_historical_pattern_analysis": EDGE, "T4_citywide_traffic_analytics": EDGE},
    FULL_OFFLOADING: {"T1_data_collection_and_filtering": CLOUD, "T2_realtime_monitoring": CLOUD,
                      "T3_historical_pattern_analysis": CLOUD, "T4_citywide_traffic_analytics": CLOUD},
    PARTIAL_OFFLOADING: {"T1_data_collection_and_filtering": EDGE, "T2_realtime_monitoring": EDGE,
                         "T3_historical_pattern_analysis": CLOUD, "T4_citywide_traffic_analytics": CLOUD},
}


# ---------------------------------------------------------------------------
# Strategy 1 - Local Execution (No Offloading)
# ---------------------------------------------------------------------------
def local_execution(raw):
    t0 = time.perf_counter()
    parts, unroutable = T.partition_by_intersection(raw)
    aggregated, patterns, timings = {}, {}, {}
    for iid in sorted(parts):
        agg, t12 = T.run_edge_tasks_for_intersection(iid, parts[iid])
        aggregated[iid] = agg
        T.add_timings(timings, t12)
    t_edge_tasks = time.perf_counter()
    for iid in sorted(aggregated):
        pattern, t3 = T.run_cloud_tasks_for_intersection(aggregated[iid])
        patterns[iid] = pattern
        T.add_timings(timings, t3)
    t4 = time.perf_counter()
    citywide = T.cloud_citywide_traffic_analytics(aggregated, patterns)
    timings["T4_citywide_traffic_analytics"] = time.perf_counter() - t4
    result = T.assemble_result(aggregated, patterns, citywide, unroutable)
    edge_exec = time.perf_counter() - t0
    return result, {
        "edge_execution_s": edge_exec,
        "edge_layer_tasks_s": t_edge_tasks - t0,
        "cloud_execution_s": 0.0,
        "communication_s": 0.0,
        "bytes_to_cloud": 0, "bytes_from_cloud": 0,
        "subtask_times_s": timings,
    }, aggregated


# ---------------------------------------------------------------------------
# Strategy 2 - Full Offloading
# ---------------------------------------------------------------------------
def full_offloading(raw, cloud_url, link, task_id):
    resp, net = post_json(f"{cloud_url}/cloud/offload/full",
                          {"task_id": task_id, "raw_sensor_telemetry": raw}, link)
    return resp["result"], {
        "edge_execution_s": 0.0,
        "cloud_execution_s": resp["cloud_execution_s"],
        "cloud_request_parse_s": resp["cloud_request_parse_s"],
        "transfer": net,
        "bytes_to_cloud": net["bytes_sent"], "bytes_from_cloud": net["bytes_received"],
        "subtask_times_s": resp["subtask_cpu_s"],
        "cloud_workers": resp.get("workers"),
    }, None


# ---------------------------------------------------------------------------
# Strategy 3 - Partial Offloading
# ---------------------------------------------------------------------------
def partial_offloading(raw, cloud_url, link, task_id):
    # Part A - Edge Layer tasks on the Edge Node (Sense, Filter, Process)
    t0 = time.perf_counter()
    parts, unroutable = T.partition_by_intersection(raw)
    aggregated, timings = {}, {}
    for iid in sorted(parts):
        agg, t12 = T.run_edge_tasks_for_intersection(iid, parts[iid])
        aggregated[iid] = agg
        T.add_timings(timings, t12)
    edge_exec = time.perf_counter() - t0

    # Part B - Cloud Layer tasks offloaded with only the Aggregated Traffic Data
    payload = {"task_id": task_id,
               "aggregated_traffic_data": {i: {k: v for k, v in a.items() if k != "filter_stats"}
                                           for i, a in aggregated.items()}}
    resp, net = post_json(f"{cloud_url}/cloud/offload/partial", payload, link)
    T.add_timings(timings, resp["subtask_cpu_s"])
    result = T.assemble_result(aggregated, resp["historical_traffic_patterns"],
                               resp["citywide_traffic_analytics"], unroutable)
    return result, {
        "edge_execution_s": edge_exec,
        "cloud_execution_s": resp["cloud_execution_s"],
        "cloud_request_parse_s": resp["cloud_request_parse_s"],
        "transfer": net,
        "bytes_to_cloud": net["bytes_sent"], "bytes_from_cloud": net["bytes_received"],
        "subtask_times_s": timings,
        "cloud_workers": resp.get("workers"),
    }, aggregated


# ---------------------------------------------------------------------------
# Offloading Decision Engine (when / why to offload)
# ---------------------------------------------------------------------------
class OffloadingDecisionEngine:
    """Analytical cost model.  All costs are in seconds per raw sensor reading."""

    ALPHA = 0.5                       # EMA weight for re-calibration

    def __init__(self, link):
        self.link = link
        # initial guesses; replaced by measurements (calibrate() and observe())
        self.c_edge_layer_tasks = 2.0e-5       # T1+T2 on the Edge Node
        self.c_cloud_layer_tasks_edge = 6.0e-5 # T3+T4 on the Edge Node
        self.c_cloud_full = 1.5e-5             # T1-T4 on the Cloud Node (parallel workers)
        self.c_cloud_partial = 1.0e-5          # T3+T4 on the Cloud Node
        self.c_serialize = 3.0e-6              # JSON encode/decode per reading (both ends)
        self.raw_bytes_per_reading = 210.0
        self.agg_bytes_per_reading = 15.0
        self.samples = 0

    def _ema(self, attr, value):
        if value is not None and value > 0:
            setattr(self, attr, (1 - self.ALPHA) * getattr(self, attr) + self.ALPHA * value)

    def observe(self, strategy, n, m):
        """Re-calibrate from a finished task."""
        if n <= 0:
            return
        self.samples += 1
        if strategy == LOCAL_EXECUTION:
            self._ema("c_edge_layer_tasks", m["edge_layer_tasks_s"] / n)
            self._ema("c_cloud_layer_tasks_edge", (m["edge_execution_s"] - m["edge_layer_tasks_s"]) / n)
        elif strategy == FULL_OFFLOADING:
            self._ema("c_cloud_full", m["cloud_execution_s"] / n)
            self._ema("raw_bytes_per_reading", m["bytes_to_cloud"] / n)
        elif strategy == PARTIAL_OFFLOADING:
            self._ema("c_edge_layer_tasks", m["edge_execution_s"] / n)
            self._ema("c_cloud_partial", m["cloud_execution_s"] / n)
            self._ema("agg_bytes_per_reading", m["bytes_to_cloud"] / n)

    def _network_time(self, nbytes):
        """RTT for request/response + TCP slow-start rounds + transmission time."""
        rtt = self.link.rtt_ms / 1000
        bw = self.link.bandwidth_mbit * 1e6
        slow_start_rounds = math.log2(1 + nbytes / 14_600)  # initial cwnd = 10 x 1460 B
        if self.link.mode == "app":
            slow_start_rounds = 0                          # app emulation: one RTT per exchange
        return rtt * (1 + slow_start_rounds) + nbytes * 8 / bw

    def estimate(self, n):
        raw_b = n * self.raw_bytes_per_reading
        agg_b = n * self.agg_bytes_per_reading
        local = n * (self.c_edge_layer_tasks + self.c_cloud_layer_tasks_edge)
        full = (n * self.c_serialize + self._network_time(raw_b) + n * self.c_cloud_full)
        partial = (n * self.c_edge_layer_tasks + self._network_time(agg_b)
                   + n * self.c_cloud_partial + n * self.c_serialize * 0.1)
        return {LOCAL_EXECUTION: local, FULL_OFFLOADING: full, PARTIAL_OFFLOADING: partial}, raw_b, agg_b

    def decide(self, n):
        est, raw_b, agg_b = self.estimate(n)
        best = min(est, key=est.get)
        local = est[LOCAL_EXECUTION]
        if best == LOCAL_EXECUTION:
            why = (f"Keep the task on the Edge: local compute {local*1000:.0f} ms is cheaper than paying the "
                   f"WAN round trip ({self.link.rtt_ms:.0f} ms RTT) plus transfer; low latency wins for small tasks.")
        elif best == FULL_OFFLOADING:
            why = (f"Offload everything: the Edge would need {local*1000:.0f} ms, the Cloud's parallel workers "
                   f"finish faster and the {raw_b/1e6:.2f} MB raw transfer fits the "
                   f"{self.link.bandwidth_mbit:.0f} Mbit/s link.")
        else:
            why = (f"Split the task: Edge filters/aggregates first (raw {raw_b/1e6:.2f} MB -> {agg_b/1e6:.2f} MB, "
                   f"{100*(1-agg_b/raw_b):.0f}% less WAN traffic), the Cloud runs the heavy historical analysis.")
        return {"recommended_strategy": best,
                "recommended_title": STRATEGY_TITLE[best],
                "estimated_response_time_s": {k: round(v, 4) for k, v in est.items()},
                "reason": why,
                "inputs": {"num_readings": n, "rtt_ms": self.link.rtt_ms,
                           "bandwidth_mbit": self.link.bandwidth_mbit,
                           "raw_payload_mb": round(raw_b / 1e6, 3),
                           "aggregated_payload_mb": round(agg_b / 1e6, 3),
                           "calibration_samples": self.samples}}


def new_task_id():
    return "TASK-" + uuid.uuid4().hex[:8].upper()
