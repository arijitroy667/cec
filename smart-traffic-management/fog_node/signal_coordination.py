"""
Fog Layer task T5 - Real-Time Traffic Flow Optimization (local).

Slide 31: "Fog Layer: Coordinates signal timings dynamically across 10
adjacent intersections to smooth regional traffic flow."

Deterministic numerical method (no prediction, no ML):
  1. Webster's optimal cycle length per intersection
        C0 = (1.5 L + 5) / (1 - Y),   Y = sum of critical flow ratios y = q / s
  2. Common cycle for the district (= largest C0, clamped to the Central
     Control Commands min/max) so that all 10 adjacent intersections run in
     one coordinated group.
  3. Green splits proportional to the critical flow ratios.
  4. Green-wave offsets along the East-West corridor:
        offset_i = (distance_i / progression speed) mod C
  5. Event handling: Emergency Vehicle Prioritization (green pre-emption on
     the approach of the emergency vehicle) and accident reroute advisories
     to the neighbouring intersections (V2X message processing).
"""
from __future__ import annotations

from common.terminology import (
    PHASE_NS, PHASE_EW, INTERSECTION_SPACING_M, EVENT_ACCIDENT,
)


def webster_cycle(y_total, lost_time_s):
    if y_total >= 0.95:                      # over-saturated: Webster not defined
        return None
    return (1.5 * lost_time_s + 5) / (1 - y_total)


def coordinate_signal_timings(intersections, events, policy):
    """intersections: list of current-state dicts from the Edge Layer
    (approach_flow_vph, speed_kmh, emergency_vehicle_approach, ...).
    Returns the Local Control Commands for every intersection."""
    sat = policy["saturation_flow_vph_per_lane"] * policy["lanes_per_approach"]
    lost = 2 * policy["lost_time_per_phase_s"]
    cmin, cmax = policy["min_cycle_s"], policy["max_cycle_s"]

    plans = []
    for st in sorted(intersections, key=lambda s: s["intersection_id"]):
        q = st.get("approach_flow_vph", {})
        y_ns = max(q.get("N", 0), q.get("S", 0)) / sat
        y_ew = max(q.get("E", 0), q.get("W", 0)) / sat
        y = y_ns + y_ew
        c0 = webster_cycle(y, lost)
        plans.append({"intersection_id": st["intersection_id"], "y_ns": y_ns, "y_ew": y_ew,
                      "Y": y, "webster_cycle_s": c0, "oversaturated": c0 is None,
                      "speed_kmh": st.get("speed_kmh") or policy["corridor_speed_limit_kmh"],
                      "emergency_vehicle_approach": st.get("emergency_vehicle_approach")})

    # Common cycle of the coordinated group
    cycles = [p["webster_cycle_s"] if p["webster_cycle_s"] else cmax for p in plans]
    common_cycle = round(min(cmax, max(cmin, max(cycles) if cycles else cmin)))

    # Progression speed of the green wave
    speeds = [p["speed_kmh"] for p in plans if p["speed_kmh"]]
    prog_kmh = min(policy["corridor_speed_limit_kmh"], sum(speeds) / len(speeds)) if speeds else \
        policy["corridor_speed_limit_kmh"]
    prog_ms = prog_kmh / 3.6

    accident_sites = {e["intersection_id"] for e in events if e.get("type") == EVENT_ACCIDENT}
    order = [p["intersection_id"] for p in plans]

    commands = []
    for idx, p in enumerate(plans):
        effective_green = common_cycle - lost
        y = p["Y"] or 1e-9
        g_ns = max(7.0, effective_green * p["y_ns"] / y) if p["Y"] > 0 else effective_green / 2
        g_ew = max(7.0, effective_green - g_ns)
        offset = (idx * INTERSECTION_SPACING_M / prog_ms) % common_cycle
        cmd = {"intersection_id": p["intersection_id"],
               "cycle_s": common_cycle,
               "green_s": {PHASE_NS: round(g_ns, 1), PHASE_EW: round(g_ew, 1)},
               "green_wave_offset_s": round(offset, 1),
               "webster_cycle_s": round(p["webster_cycle_s"], 1) if p["webster_cycle_s"] else None,
               "critical_flow_ratio_Y": round(p["Y"], 3),
               "oversaturated": p["oversaturated"],
               "actions": []}
        if p["emergency_vehicle_approach"]:
            a = p["emergency_vehicle_approach"]
            cmd["actions"].append({"action": "EMERGENCY_VEHICLE_PREEMPTION",
                                   "green_phase": PHASE_NS if a in ("N", "S") else PHASE_EW,
                                   "approach": a})
        for nb in (idx - 1, idx + 1):
            if 0 <= nb < len(order) and order[nb] in accident_sites:
                cmd["actions"].append({"action": "V2X_REROUTE_ADVISORY",
                                       "reason": f"accident at {order[nb]}"})
        commands.append(cmd)

    return {"common_cycle_s": common_cycle,
            "progression_speed_kmh": round(prog_kmh, 1),
            "coordinated_intersections": len(commands),
            "local_control_commands": commands}
