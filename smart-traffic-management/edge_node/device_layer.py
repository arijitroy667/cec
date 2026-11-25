"""
Device Layer simulator - Raw Sensor Telemetry from the Roadside Units (RSUs).

Each of the 10 adjacent intersections has one Roadside Unit (RSU) connected to
three sensor types (smart camera, induction loop, radar) on each of the four
approaches (N, S, E, W).  Every report says how many vehicles were counted in
the last 60 s, their mean speed and the detector occupancy.

The traffic itself follows a realistic 24-hour pattern (AM and PM peaks) and
the Greenshields speed-density relationship.  On purpose, the stream also
contains what real sensor networks produce: ~2 % faulty readings, ~1 %
duplicate transmissions, an accident at INT-07 in the evening peak, recurrent
congestion at INT-04 and a few emergency vehicles.  The Edge Layer tasks must
filter and detect these.
"""
from __future__ import annotations

import math
import random

from common.terminology import (
    ADJACENT_INTERSECTIONS, APPROACHES, SENSOR_TYPES, SMART_CAMERA,
)

DAY_START = 1_767_225_600          # 2026-01-01 00:00:00 UTC - one 24-hour history window
FREE_FLOW_SPEED = 60.0             # km/h, urban arterial
JAM_DENSITY = 120.0                # veh/km per approach (2 lanes)
CAPACITY = FREE_FLOW_SPEED * JAM_DENSITY / 4   # 1800 veh/h per approach

# Relative demand of each intersection (INT-04 and INT-07 are bottlenecks)
DEMAND_FACTOR = {"INT-01": 0.80, "INT-02": 0.90, "INT-03": 1.00, "INT-04": 1.70, "INT-05": 1.05,
                 "INT-06": 0.95, "INT-07": 1.35, "INT-08": 1.10, "INT-09": 0.85, "INT-10": 0.75}
# Main corridor runs East-West; side streets North-South
APPROACH_FACTOR = {"E": 1.30, "W": 1.25, "N": 0.60, "S": 0.55}
SENSOR_NOISE = {"smart_camera": 0.06, "induction_loop": 0.03, "radar": 0.05}
ACCIDENT = ("INT-07", 17.5, 18.25)          # intersection, start hour, end hour


def demand_vph(hour):
    """Daily demand profile (veh/h per approach before scaling)."""
    return (150
            + 700 * math.exp(-((hour - 8.5) / 1.3) ** 2)
            + 800 * math.exp(-((hour - 17.75) / 1.6) ** 2)
            + 250 * math.exp(-((hour - 13.0) / 2.5) ** 2))


def traffic_state(q):
    """Greenshields model: flow q (veh/h) -> (speed km/h, occupancy %)."""
    if q < CAPACITY:
        k = JAM_DENSITY / 2 * (1 - math.sqrt(1 - q / CAPACITY))      # uncongested branch
    else:
        over = min((q - CAPACITY) / CAPACITY, 0.9)
        k = JAM_DENSITY / 2 * (1 + over)                               # congested branch
    v = FREE_FLOW_SPEED * (1 - k / JAM_DENSITY)
    return max(v, 3.0), min(100.0, k / JAM_DENSITY * 100 * 0.9)


def generate_raw_sensor_telemetry(num_readings, seed=42):
    """Return `num_readings` raw sensor readings (list of JSON-ready dicts)
    covering a 24-hour window for the 10 adjacent intersections."""
    rng = random.Random(seed)
    per_timestamp = len(ADJACENT_INTERSECTIONS) * len(APPROACHES) * len(SENSOR_TYPES)
    steps = max(4, num_readings // per_timestamp)
    spacing = 86400 // steps
    readings = []
    for step in range(steps):
        ts = DAY_START + step * spacing
        hour = (step * spacing) / 3600
        for iid in ADJACENT_INTERSECTIONS:
            accident = iid == ACCIDENT[0] and ACCIDENT[1] <= hour < ACCIDENT[2]
            emergency_approach = None
            if rng.random() < 0.01 or (iid == "INT-03" and step == steps - 1):
                emergency_approach = rng.choice(APPROACHES)
            for a in APPROACHES:
                q = demand_vph(hour) * DEMAND_FACTOR[iid] * APPROACH_FACTOR[a]
                q *= rng.uniform(0.92, 1.08)
                speed, occ = traffic_state(q)
                if accident:
                    q, speed, occ = q * 0.25, rng.uniform(4, 8), rng.uniform(80, 95)
                served = min(q, CAPACITY) if not accident else q
                count_mu = served * 60 / 3600
                for sensor in SENSOR_TYPES:
                    noise = SENSOR_NOISE[sensor]
                    readings.append({
                        "intersection_id": iid,
                        "rsu_id": f"RSU-{iid[-2:]}",
                        "sensor_type": sensor,
                        "timestamp": ts,
                        "approach": a,
                        "vehicle_count": max(0, int(round(rng.gauss(count_mu, math.sqrt(count_mu + 1) * 0.5)))),
                        "speed_kmh": round(max(0.5, speed * (1 + rng.gauss(0, noise))), 2),
                        "occupancy_pct": round(min(100.0, max(0.0, occ * (1 + rng.gauss(0, noise)))), 2),
                        "emergency_vehicle": 1 if (sensor == SMART_CAMERA and emergency_approach == a) else 0,
                    })
    _inject_sensor_faults(readings, rng)
    return readings[:num_readings] if len(readings) > num_readings else readings


def _inject_sensor_faults(readings, rng):
    n = len(readings)
    for i in rng.sample(range(n), k=max(1, n // 50)):           # ~2 % faulty readings
        r = readings[i]
        fault = rng.randrange(5)
        if fault == 0:
            r["vehicle_count"] = -rng.randint(1, 20)
        elif fault == 1:
            r["speed_kmh"] = 999.0
        elif fault == 2:
            r["occupancy_pct"] = 140.0
        elif fault == 3:
            r["speed_kmh"] = None
        else:
            r["sensor_type"] = "unknown_sensor"
    for i in rng.sample(range(n), k=max(1, n // 100)):          # ~1 % duplicate transmissions
        readings.append(dict(readings[i]))
    rng.shuffle(readings)                                         # network arrival order
