"""
Smart Traffic Management - the computational task and its sub-tasks.

The task processes one batch of Raw Sensor Telemetry collected from the
smart cameras, induction loops and radar of 10 adjacent intersections and
produces (a) real-time vehicle counts and congestion for every intersection
and (b) city-wide historical traffic patterns for long-term urban planning.

The task is split into sub-tasks that follow the Edge Analytics Pipeline
(slide 28) and the Smart Traffic Management layers (slide 31):

  Edge Layer tasks (Edge Tasks, slide 26)
    T1  edge_data_collection_and_filtering   Sense + Filter     -> data collection
    T2  edge_realtime_monitoring             Process            -> monitoring
  Cloud Layer tasks (Cloud Tasks, slide 26)
    T3  cloud_historical_pattern_analysis    Analyze            -> numerical computation
    T4  cloud_citywide_traffic_analytics     Analyze            -> data analysis
  Fog Layer task (runs only on the Fog Node, see fog_node/signal_coordination.py)
    T5  fog signal timing coordination across 10 adjacent intersections -> Act

No prediction and no machine learning is used: everything is filtering,
counting, descriptive statistics and deterministic numerical methods.

All functions are pure (no I/O) so the *same* code runs on the Edge Node
and on the Cloud Node.  That is what makes computational offloading
possible: the task can be executed at either node and gives identical
results.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
import time
from collections import defaultdict

from common.terminology import (
    ADJACENT_INTERSECTIONS, APPROACHES, SENSOR_TYPES,
    FREE_FLOW, MODERATE, HEAVY, SEVERE,
    EVENT_ACCIDENT, EVENT_CONGESTION, EVENT_EMERGENCY_VEHICLE,
)

MONITORING_INTERVAL_S = 900        # 15-minute monitoring intervals
COUNT_WINDOW_S = 60                # each sensor reports vehicles counted in the last 60 s
BOOTSTRAP_RESAMPLES = 200          # resamples for the 85th-percentile-speed confidence interval
REQUIRED_FIELDS = ("intersection_id", "sensor_type", "timestamp", "approach",
                   "vehicle_count", "speed_kmh", "occupancy_pct")


def _r(x, nd=3):
    return None if x is None else round(float(x), nd)


# ===========================================================================
# Partitioning helper: one sub-task instance per intersection
# ===========================================================================
def partition_by_intersection(raw_readings):
    """Group raw sensor telemetry by intersection (lets the Cloud Node run
    intersections in parallel).  Readings without a valid intersection id are
    counted as malformed and dropped here."""
    parts = defaultdict(list)
    unroutable = 0
    valid_ids = set(ADJACENT_INTERSECTIONS)
    for r in raw_readings:
        iid = r.get("intersection_id") if isinstance(r, dict) else None
        if iid in valid_ids:
            parts[iid].append(r)
        else:
            unroutable += 1
    return dict(parts), unroutable


# ===========================================================================
# T1 - Edge Layer: Real-Time Sensing & Data Collection  (Sense -> Filter)
# ===========================================================================
def edge_data_collection_and_filtering(readings):
    """Validate, de-duplicate and order the raw sensor telemetry of one
    intersection.  This is the 'Reduction (Filter)' stage: sensor faults and
    duplicate transmissions are removed before any further processing."""
    stats = {"received": len(readings), "malformed": 0, "out_of_range": 0,
             "duplicates": 0, "accepted": 0}
    seen = set()
    clean = []
    sensor_set = set(SENSOR_TYPES)
    approach_set = set(APPROACHES)
    for r in readings:
        if any(r.get(f) is None for f in REQUIRED_FIELDS) or r["sensor_type"] not in sensor_set \
                or r["approach"] not in approach_set:
            stats["malformed"] += 1
            continue
        c, v, o = r["vehicle_count"], r["speed_kmh"], r["occupancy_pct"]
        if not (0 <= c <= 150 and 0.0 <= v <= 160.0 and 0.0 <= o <= 100.0):
            stats["out_of_range"] += 1
            continue
        key = (r["sensor_type"], r["approach"], r["timestamp"])
        if key in seen:
            stats["duplicates"] += 1
            continue
        seen.add(key)
        clean.append(r)
    clean.sort(key=lambda r: (r["timestamp"], r["approach"], r["sensor_type"]))
    stats["accepted"] = len(clean)
    return clean, stats


# ===========================================================================
# T2 - Edge Layer: Real-time monitoring - vehicle counts & congestion  (Process)
# ===========================================================================
def classify_congestion(speed_kmh, occupancy_pct):
    if occupancy_pct >= 45 or speed_kmh < 15:
        return SEVERE
    if occupancy_pct >= 30 or speed_kmh < 25:
        return HEAVY
    if occupancy_pct >= 15 or speed_kmh < 40:
        return MODERATE
    return FREE_FLOW


def edge_realtime_monitoring(intersection_id, clean, filter_stats):
    """Sensor fusion + vehicle counts + congestion level + event detection
    (accidents, congestion, emergency vehicles) for one intersection.

    Output = 'Aggregated Traffic Data': fused per-approach samples (columnar,
    compact) and 15-minute monitoring intervals.  This is what the Edge sends
    instead of the Raw Sensor Telemetry (slide 27: Transmitted Filtered Events)."""
    # --- sensor fusion: smart camera + induction loop + radar -> one sample
    fused = {}
    for r in clean:
        key = (r["timestamp"], r["approach"])
        acc = fused.get(key)
        if acc is None:
            acc = fused[key] = [0.0, 0.0, 0.0, 0, 0]
        acc[0] += r["vehicle_count"]
        acc[1] += r["speed_kmh"]
        acc[2] += r["occupancy_pct"]
        acc[3] += 1
        if r.get("emergency_vehicle"):
            acc[4] = 1
    samples = {"t": [], "approach": [], "count": [], "speed": [], "occupancy": [], "emergency": []}
    for (t, a) in sorted(fused):
        s = fused[(t, a)]
        n = s[3]
        samples["t"].append(t)
        samples["approach"].append(a)
        samples["count"].append(_r(s[0] / n, 2))
        samples["speed"].append(_r(s[1] / n, 2))
        samples["occupancy"].append(_r(s[2] / n, 2))
        samples["emergency"].append(s[4])

    # --- 15-minute monitoring intervals: vehicle counts & congestion
    bins = {}
    for t, a, c, v, o in zip(samples["t"], samples["approach"], samples["count"],
                             samples["speed"], samples["occupancy"]):
        b = t - (t % MONITORING_INTERVAL_S)
        acc = bins.get(b)
        if acc is None:
            acc = bins[b] = {"cnt": defaultdict(float), "n": defaultdict(int),
                             "va": defaultdict(float), "oa": defaultdict(float),
                             "vw": 0.0, "w": 0.0, "occ": 0.0, "m": 0}
        acc["cnt"][a] += c
        acc["n"][a] += 1
        acc["va"][a] += v
        acc["oa"][a] += o
        acc["vw"] += v * max(c, 0.1)
        acc["w"] += max(c, 0.1)
        acc["occ"] += o
        acc["m"] += 1
    intervals = []
    events = []
    for b in sorted(bins):
        acc = bins[b]
        approach_flow = {a: _r(acc["cnt"][a] / acc["n"][a] * (3600 / COUNT_WINDOW_S), 1)
                         for a in APPROACHES if acc["n"][a]}
        speed = acc["vw"] / acc["w"]
        occ = acc["occ"] / acc["m"]
        # congestion level of the intersection = level of its critical (worst) approach
        crit_speed = min(acc["va"][a] / acc["n"][a] for a in acc["n"])
        crit_occ = max(acc["oa"][a] / acc["n"][a] for a in acc["n"])
        level = classify_congestion(crit_speed, crit_occ)
        iv = {"start": b,
              "vehicles": _r(sum(acc["cnt"].values()), 1),
              "flow_vph": _r(sum(approach_flow.values()), 1),
              "approach_flow_vph": approach_flow,
              "speed_kmh": _r(speed, 2),
              "occupancy_pct": _r(occ, 2),
              "congestion_level": level}
        intervals.append(iv)
        if crit_speed < 12 and crit_occ > 65 and speed < 20:
            events.append({"type": EVENT_ACCIDENT, "intersection_id": intersection_id, "at": b,
                           "speed_kmh": iv["speed_kmh"], "occupancy_pct": iv["occupancy_pct"]})
        elif level == SEVERE:
            events.append({"type": EVENT_CONGESTION, "intersection_id": intersection_id, "at": b,
                           "speed_kmh": iv["speed_kmh"], "occupancy_pct": iv["occupancy_pct"]})
    for t, a, e in zip(samples["t"], samples["approach"], samples["emergency"]):
        if e:
            events.append({"type": EVENT_EMERGENCY_VEHICLE, "intersection_id": intersection_id,
                           "at": t, "approach": a})

    # --- current state of the intersection (input for instant actuation / fog)
    last_t = samples["t"][-1] if samples["t"] else None
    current = dict(intervals[-1]) if intervals else {}
    current["intersection_id"] = intersection_id
    current["emergency_vehicle_approach"] = next(
        (a for t, a, e in zip(samples["t"], samples["approach"], samples["emergency"])
         if e and t == last_t), None)

    return {"intersection_id": intersection_id,
            "filter_stats": filter_stats,
            "samples": samples,
            "intervals": intervals,
            "events": events,
            "current_state": current}


def run_edge_tasks_for_intersection(intersection_id, readings):
    """T1 + T2 for one intersection.  Returns (aggregated_traffic_data, timings)."""
    t0 = time.perf_counter()
    clean, fstats = edge_data_collection_and_filtering(readings)
    t1 = time.perf_counter()
    agg = edge_realtime_monitoring(intersection_id, clean, fstats)
    t2 = time.perf_counter()
    return agg, {"T1_data_collection_and_filtering": t1 - t0, "T2_realtime_monitoring": t2 - t1}


# ===========================================================================
# T3 - Cloud Layer: historical traffic pattern analysis (numerical computation)
# ===========================================================================
def _percentile(sorted_vals, p):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * p
    f = math.floor(k)
    c = min(f + 1, len(sorted_vals) - 1)
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def _least_squares(xs, ys):
    n = len(xs)
    if n < 3:
        return None, None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None, None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    return my - slope * mx, slope


def cloud_historical_pattern_analysis(agg):
    """Cloud Layer analysis of one intersection's historical traffic data:
       * 24-hour flow profile, AM / PM peak hours
       * spot-speed study: 15th/50th/85th/95th percentile speed and a
         bootstrap 95 % confidence interval of the 85th-percentile speed
       * speed-density fundamental diagram (Greenshields, least squares)
         -> free-flow speed, jam density, approach capacity
       * congestion frequency and volume-to-capacity (v/c) ratio
    """
    iid = agg["intersection_id"]
    s = agg["samples"]
    speeds = sorted(s["speed"])

    # 24-hour profile from the 15-minute intervals
    hourly = defaultdict(list)
    for iv in agg["intervals"]:
        hourly[(iv["start"] % 86400) // 3600].append(iv["flow_vph"])
    profile = {f"{h:02d}": _r(statistics.fmean(v), 1) for h, v in sorted(hourly.items())}
    am = {h: v for h, v in profile.items() if 6 <= int(h) <= 11}
    pm = {h: v for h, v in profile.items() if 15 <= int(h) <= 20}
    am_peak = max(am, key=am.get) if am else None
    pm_peak = max(pm, key=pm.get) if pm else None

    # Spot-speed study with bootstrap confidence interval (deterministic RNG)
    rng = random.Random(int(hashlib.md5(iid.encode()).hexdigest()[:8], 16))
    n = len(speeds)
    boot = []
    for _ in range(BOOTSTRAP_RESAMPLES if n else 0):
        resample = sorted(rng.choices(speeds, k=n))
        boot.append(_percentile(resample, 0.85))
    boot.sort()

    # Fundamental diagram per approach: speed = vf - (vf/kj) * density
    fd = {}
    for a in APPROACHES:
        ks, vs = [], []
        for ap, c, v in zip(s["approach"], s["count"], s["speed"]):
            if ap == a and v > 1:
                q = c * (3600 / COUNT_WINDOW_S)          # flow, veh/h
                ks.append(q / v)                           # density, veh/km
                vs.append(v)
        vf, slope = _least_squares(ks, vs)
        if vf and slope and slope < 0:
            kj = -vf / slope
            fd[a] = {"free_flow_speed_kmh": _r(vf, 2), "jam_density_veh_km": _r(kj, 2),
                     "capacity_vph": _r(vf * kj / 4, 1), "samples": len(ks)}
        else:
            fd[a] = {"free_flow_speed_kmh": None, "jam_density_veh_km": None,
                     "capacity_vph": None, "samples": len(ks)}

    flows = [iv["flow_vph"] for iv in agg["intervals"]]
    levels = [iv["congestion_level"] for iv in agg["intervals"]]
    peak_flow = max(flows) if flows else 0
    capacity = sum(v["capacity_vph"] or 0 for v in fd.values())
    # volume-to-capacity of the critical approach (peak 15-min approach flow / approach capacity)
    vc = 0.0
    for a in APPROACHES:
        cap_a = fd[a]["capacity_vph"]
        peak_a = max((iv["approach_flow_vph"].get(a, 0) for iv in agg["intervals"]), default=0)
        if cap_a:
            vc = max(vc, peak_a / cap_a)
    congestion_freq = (sum(1 for l in levels if l in (HEAVY, SEVERE)) / len(levels)) if levels else 0

    return {"intersection_id": iid,
            "total_vehicles": _r(sum(iv["vehicles"] for iv in agg["intervals"]), 1),
            "mean_flow_vph": _r(statistics.fmean(flows), 1) if flows else None,
            "peak_flow_vph": _r(peak_flow, 1),
            "hourly_flow_profile_vph": profile,
            "am_peak_hour": am_peak,
            "pm_peak_hour": pm_peak,
            "speed_percentiles_kmh": {"p15": _r(_percentile(speeds, .15), 2),
                                      "p50": _r(_percentile(speeds, .50), 2),
                                      "p85": _r(_percentile(speeds, .85), 2),
                                      "p95": _r(_percentile(speeds, .95), 2)},
            "p85_speed_95ci_kmh": [_r(_percentile(boot, .025), 2), _r(_percentile(boot, .975), 2)],
            "fundamental_diagram": fd,
            "estimated_capacity_vph": _r(capacity, 1),
            "volume_to_capacity": _r(vc, 3),
            "congestion_frequency": _r(congestion_freq, 3),
            "accidents_detected": sum(1 for e in agg["events"] if e["type"] == EVENT_ACCIDENT)}


# ===========================================================================
# T4 - Cloud Layer: city-wide traffic analytics for long-term urban planning
# ===========================================================================
def _pearson(x, y):
    n = len(x)
    if n < 3:
        return None
    mx, my = sum(x) / n, sum(y) / n
    sx = math.sqrt(sum((a - mx) ** 2 for a in x))
    sy = math.sqrt(sum((b - my) ** 2 for b in y))
    if sx == 0 or sy == 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (sx * sy)


def cloud_citywide_traffic_analytics(aggregated, patterns):
    """Combines all intersections: flow correlation matrix (how strongly
    adjacent intersections are coupled), corridor peak interval, bottleneck
    ranking and rule-based recommendations for long-term urban planning."""
    ids = sorted(aggregated)
    series = {i: {iv["start"]: iv["flow_vph"] for iv in aggregated[i]["intervals"]} for i in ids}
    common = sorted(set.intersection(*(set(v) for v in series.values()))) if ids else []
    matrix = {}
    for a in ids:
        matrix[a] = {}
        for b in ids:
            matrix[a][b] = _r(_pearson([series[a][t] for t in common], [series[b][t] for t in common]), 3)

    corridor = defaultdict(float)
    for i in ids:
        for t, f in series[i].items():
            corridor[t] += f
    peak_t = max(corridor, key=corridor.get) if corridor else None

    ranking = []
    for i in ids:
        p = patterns[i]
        vc = p["volume_to_capacity"] or 0
        score = 0.6 * p["congestion_frequency"] + 0.4 * min(vc, 1.5) / 1.5
        recs = []
        if vc >= 0.95:
            recs.append("Capacity upgrade candidate (add turning lane / widen approach)")
        if p["congestion_frequency"] >= 0.25:
            recs.append("Priority site for adaptive signal control")
        if p["accidents_detected"]:
            recs.append("Road safety audit (accidents detected)")
        idx = ids.index(i)
        for j in (idx - 1, idx + 1):
            if 0 <= j < len(ids) and (matrix[i][ids[j]] or 0) >= 0.9:
                recs.append(f"Keep in green-wave coordination group with {ids[j]}")
        ranking.append({"intersection_id": i, "bottleneck_score": _r(score, 3),
                        "volume_to_capacity": p["volume_to_capacity"],
                        "congestion_frequency": p["congestion_frequency"],
                        "urban_planning_recommendations": recs})
    ranking.sort(key=lambda x: (-x["bottleneck_score"], x["intersection_id"]))

    return {"flow_correlation_matrix": matrix,
            "corridor_peak_interval_start": peak_t,
            "corridor_peak_flow_vph": _r(corridor[peak_t], 1) if peak_t is not None else None,
            "bottleneck_ranking": ranking}


def run_cloud_tasks_for_intersection(agg):
    t0 = time.perf_counter()
    res = cloud_historical_pattern_analysis(agg)
    return res, {"T3_historical_pattern_analysis": time.perf_counter() - t0}


# ===========================================================================
# Result assembly & integrity digest
# ===========================================================================
def compact_edge_output(agg):
    """What the Edge Node keeps / returns for real-time use (no raw samples)."""
    return {"intersection_id": agg["intersection_id"],
            "filter_stats": agg["filter_stats"],
            "current_state": agg["current_state"],
            "events": agg["events"],
            "intervals": len(agg["intervals"])}


def assemble_result(aggregated, patterns, citywide, unroutable):
    ids = sorted(aggregated)
    filt = {"received": unroutable, "malformed": unroutable, "out_of_range": 0, "duplicates": 0, "accepted": 0}
    for i in ids:
        for k, v in aggregated[i]["filter_stats"].items():
            filt[k] += v
    return {"realtime_monitoring": {i: compact_edge_output(aggregated[i]) for i in ids},
            "data_collection_summary": filt,
            "historical_traffic_patterns": {i: patterns[i] for i in ids},
            "citywide_traffic_analytics": citywide}


def result_digest(result):
    """SHA-256 of the analytical result.  Identical digests across Local,
    Full and Partial Offloading prove that offloading changes *where* the
    task runs, not *what* it computes."""
    canonical = json.loads(json.dumps(result))          # same form as after a JSON transfer
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()[:16]


def add_timings(total, part):
    for k, v in part.items():
        total[k] = total.get(k, 0.0) + v
    return total
