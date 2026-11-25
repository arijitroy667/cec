# Smart Traffic Management System — Edge / Fog / Cloud Computational Offloading

Assignment 2 · Cloud and Edge Computing (builds on the Ubuntu VM + Docker setup of Assignment 1)

The IoT application is the **Smart Traffic Management System** from the course slides
("III – Edge Computing Fundamentals", slide 31):

| Layer | Container | Role (slide 31) |
|---|---|---|
| **Edge Layer** — Data Acquisition & Actuation | `edge-node` :8000 (host) → 5000 | Smart cameras perform intersection video object detection locally, identifying vehicle counts and congestion in real time. |
| **Fog Layer** — Distributed Intelligence | `fog-node` :6000 | Coordinates signal timings dynamically across 10 adjacent intersections to smooth regional traffic flow. |
| **Cloud Layer** — Centralized Intelligence (Central TMS) | `cloud-node` :7000 | Analyzes city-wide historical traffic patterns for long-term urban planning. |

The Device Layer (smart cameras, induction loops and radar at the Roadside Units of 10
adjacent intersections) is simulated inside the Edge Node.

## Computational tasks (no prediction, no machine learning)

| ID | Category | Task | Pipeline stage (slide 28) | Layer |
|---|---|---|---|---|
| T1 | Data collection | Validate, de-duplicate and order raw sensor telemetry (~2 % faults, ~1 % duplicates) | Sense → Filter | Edge |
| T2 | Monitoring | Sensor fusion, vehicle counts, flow, speed, occupancy, congestion level, event detection (accidents, congestion, emergency vehicles) | Process | Edge |
| T3 | Numerical computation | Per-intersection 24 h profile, AM/PM peak, 85th-percentile speed + bootstrap 95 % CI, Greenshields fundamental diagram (least squares) → capacity, v/c ratio | Analyze | Cloud |
| T4 | Data analysis | City-wide flow correlation matrix, corridor peak, bottleneck ranking, urban-planning recommendations | Analyze | Cloud |
| T5 | Numerical computation | Webster's optimal cycle, green splits, green-wave offsets, emergency-vehicle pre-emption | Act | **Fog** (always) |

## Offloading strategies (Edge ↔ Cloud)

| Strategy | T1 | T2 | T3 | T4 | Data sent Edge → Cloud |
|---|---|---|---|---|---|
| Local Execution (No Offloading) | Edge | Edge | Edge | Edge | nothing |
| Full Offloading | Cloud | Cloud | Cloud | Cloud | all raw sensor telemetry |
| Partial Offloading | Edge | Edge | Cloud | Cloud | aggregated traffic data only (~7 % of raw) |
| `auto` | chosen by the **Offloading Decision Engine** (analytical cost model) | | | | |

All strategies return bit-identical results (same SHA-256 `result_digest`).

## Project layout

```
common/          terminology.py (slide vocabulary), traffic_tasks.py (T1-T4), transport.py (JSON over HTTP)
edge_node/       edge_app.py (Flask), device_layer.py, offloading.py, network_emulation.py, Dockerfile
fog_node/        fog_app.py (Flask), signal_coordination.py (T5), Dockerfile
cloud_node/      cloud_app.py (Flask, process pool, Traffic Data Lake), Dockerfile
benchmark/       demo_offloading.py, run_benchmark.py, docker_stats_monitor.py, plot_results.py
scripts/         run_all.sh (every Docker command in order)
docker-compose.yml
results/         sample benchmark output (CSV, JSON, charts)
```

## Run it (Ubuntu VM from Assignment 1)

```bash
cd smart-traffic-management
docker compose up -d --build              # build 3 images, start 3 containers
docker compose ps
curl http://localhost:8000/health          # edge-node
curl http://localhost:6000/health          # fog-node
curl http://localhost:7000/health          # cloud-node

# one task with a chosen strategy
curl -X POST http://localhost:8000/edge/run-task -H "Content-Type: application/json" \
     -d '{"strategy":"partial","workload":"large"}'

pip3 install requests matplotlib           # on the VM (host) only
python3 benchmark/demo_offloading.py --workload large     # demonstration of all strategies
python3 benchmark/run_benchmark.py --reps 5               # performance evaluation + Docker Stats
python3 benchmark/plot_results.py --results results       # charts
docker stats edge-node fog-node cloud-node
docker compose down
```

Or simply `bash scripts/run_all.sh`.

## Network and resource model

Containers on one host are ~0.1 ms apart, so the links are emulated with the slide values:
Edge↔Cloud RTT 100 ms / 50 Mbit/s, Edge↔Fog 10 ms / 100 Mbit/s, Fog↔Cloud 60 ms / 200 Mbit/s
(environment variables in `docker-compose.yml`; change at runtime with `POST /edge/network`).
`WAN_EMULATION=app` (default) adds propagation + transmission delay in the transport layer;
`WAN_EMULATION=tc` uses `tc netem` instead (needs `sudo modprobe sch_netem` on the VM).

CPU/memory limits follow slide 23: Edge 0.5 CPU / 512 MB (constrained), Fog 1 CPU,
Cloud 2 CPU / 2 GB with 2 worker processes.
