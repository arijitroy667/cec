"""
Shared vocabulary for the Smart Traffic Management System (TMS).

Every name below is taken from the course slides "III - Edge Computing
Fundamentals" so that the source code speaks the same language as the
lecture material:

  * Slide 31 - Smart Traffic Management (Edge Layer / Fog Layer / Cloud Layer)
  * Slide 28 - Edge Analytics Pipeline (Sense -> Filter -> Process -> Analyze -> Act)
  * Slide 27 - Data Processing: Cloud vs Edge (Raw Sensor Telemetry, Transmitted Filtered Events)
  * Slide 26 - Edge Complements the Cloud (Edge Tasks / Cloud Tasks)

The three layers are kept strictly separate:
  Edge  is Edge  - the Edge Node container  (data acquisition & actuation)
  Fog   is Fog   - the Fog Node container   (distributed intelligence)
  Cloud is Cloud - the Cloud Node container (centralized intelligence)
"""

# ---------------------------------------------------------------------------
# Layers of the Smart Traffic Management System (slide 31)
# ---------------------------------------------------------------------------
DEVICE_LAYER = "Device Layer"   # smart cameras, induction loops, radar at Roadside Units (RSUs)
EDGE_LAYER = "Edge Layer"       # Data Acquisition & Actuation - ultra-low latency, immediate response
FOG_LAYER = "Fog Layer"         # Distributed Intelligence - low latency distributed processing
CLOUD_LAYER = "Cloud Layer"     # Centralized Intelligence - Central Traffic Management System (TMS)

LAYER_ROLE = {
    EDGE_LAYER: "Smart cameras perform intersection video object detection locally, "
                "identifying vehicle counts and congestion in real time.",
    FOG_LAYER: "Coordinates signal timings dynamically across 10 adjacent intersections "
               "to smooth regional traffic flow.",
    CLOUD_LAYER: "Analyzes city-wide historical traffic patterns for long-term urban planning.",
}

# ---------------------------------------------------------------------------
# Edge Analytics Pipeline (slide 28)
# ---------------------------------------------------------------------------
STAGE_SENSE = "1. Acquisition (Sense)"
STAGE_FILTER = "2. Reduction (Filter)"
STAGE_PROCESS = "3. Transformation (Process)"
STAGE_ANALYZE = "4. Inference (Analyze)"
STAGE_ACT = "5. Execution (Act)"

# ---------------------------------------------------------------------------
# Device Layer sensors (slide 31, Edge Layer strip: "Sensors (Cameras, LiDAR,
# Radar, Induction Loops)")
# ---------------------------------------------------------------------------
SMART_CAMERA = "smart_camera"
INDUCTION_LOOP = "induction_loop"
RADAR = "radar"
SENSOR_TYPES = (SMART_CAMERA, INDUCTION_LOOP, RADAR)

APPROACHES = ("N", "S", "E", "W")          # intersection approaches
PHASE_NS = "NS"                             # north-south signal phase
PHASE_EW = "EW"                             # east-west signal phase

# ---------------------------------------------------------------------------
# District of 10 adjacent intersections handled by one fog node (slide 31)
# ---------------------------------------------------------------------------
DISTRICT_ID = "DISTRICT-01"
ADJACENT_INTERSECTIONS = tuple(f"INT-{i:02d}" for i in range(1, 11))
INTERSECTION_SPACING_M = 400               # distance between adjacent intersections

# ---------------------------------------------------------------------------
# Congestion levels used by real-time monitoring at the Edge Layer
# ---------------------------------------------------------------------------
FREE_FLOW = "Free Flow"
MODERATE = "Moderate"
HEAVY = "Heavy"
SEVERE = "Severe"
CONGESTION_LEVELS = (FREE_FLOW, MODERATE, HEAVY, SEVERE)

# Event detection (accidents, congestion) - slide 31, Single Fog Node box
EVENT_ACCIDENT = "ACCIDENT"
EVENT_CONGESTION = "CONGESTION"
EVENT_EMERGENCY_VEHICLE = "EMERGENCY_VEHICLE"

# ---------------------------------------------------------------------------
# Messages exchanged between layers (arrows on slide 31)
# ---------------------------------------------------------------------------
MSG_RAW_SENSOR_DATA_AND_ACTUATION_REQUESTS = "Raw Sensor Data & Actuation Requests"   # Edge -> Fog
MSG_LOCAL_CONTROL_COMMANDS = "Local Control Commands"                                 # Fog  -> Edge
MSG_AGGREGATED_TRAFFIC_DATA_AND_ANALYTICS = "Aggregated Traffic Data & Analytics"     # Fog  -> Cloud
MSG_CENTRAL_CONTROL_COMMANDS = "Central Control Commands"                             # Cloud -> Fog

# ---------------------------------------------------------------------------
# Computational offloading strategies (assignment 2)
# ---------------------------------------------------------------------------
LOCAL_EXECUTION = "local"        # Local Execution (No Offloading) - whole task on the Edge Node
FULL_OFFLOADING = "full"         # Full Offloading - whole task on the Cloud Node
PARTIAL_OFFLOADING = "partial"   # Partial Offloading - Edge tasks at Edge, Cloud tasks at Cloud
AUTO_OFFLOADING = "auto"         # Offloading Decision Engine chooses one of the three
STRATEGIES = (LOCAL_EXECUTION, FULL_OFFLOADING, PARTIAL_OFFLOADING)

STRATEGY_TITLE = {
    LOCAL_EXECUTION: "Local Execution (No Offloading)",
    FULL_OFFLOADING: "Full Offloading",
    PARTIAL_OFFLOADING: "Partial Offloading",
    AUTO_OFFLOADING: "Adaptive (Offloading Decision Engine)",
}

# Workload sizes = number of raw sensor readings in one task
WORKLOADS = {
    "small": 2_400,
    "medium": 24_000,
    "large": 96_000,
}
