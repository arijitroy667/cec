#!/usr/bin/env bash
# ===========================================================================
# Smart Traffic Management System - every Docker command of Assignment 2, in order.
# Run from the project root on the Ubuntu VM:   bash scripts/run_all.sh
# ===========================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

echo "### 1. Docker environment (from Assignment 1)"
docker --version
docker compose version

echo "### 2. Build the Edge, Fog and Cloud images"
docker build -f edge_node/Dockerfile  -t stms-edge-node:1.0  .
docker build -f fog_node/Dockerfile   -t stms-fog-node:1.0   .
docker build -f cloud_node/Dockerfile -t stms-cloud-node:1.0 .
docker images | grep stms

echo "### 3. Start the containers (3 networks, CPU/memory limits)"
docker compose up -d
sleep 5
docker compose ps
docker network ls | grep smart-traffic
docker inspect edge-node --format 'edge-node  CPUs={{.HostConfig.NanoCpus}} Mem={{.HostConfig.Memory}}'
docker inspect cloud-node --format 'cloud-node CPUs={{.HostConfig.NanoCpus}} Mem={{.HostConfig.Memory}}'

echo "### 4. Health checks"
curl -s http://localhost:8000/health; echo
curl -s http://localhost:6000/health; echo
curl -s http://localhost:7000/health; echo

echo "### 5. Edge -> Cloud communication: one task per strategy"
for s in local full partial auto; do
  curl -s -X POST http://localhost:8000/edge/run-task -H "Content-Type: application/json" \
       -d "{\"strategy\":\"$s\",\"workload\":\"medium\"}" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['strategy_title'], '| exec', d['task_execution_time_s'], 's | e2e', d['end_to_end_response_time_s'], 's | to cloud', d['bytes_to_cloud'], 'B | digest', d['result_digest'])"
done

echo "### 6. Demonstration + performance evaluation (needs: pip3 install requests matplotlib)"
python3 benchmark/demo_offloading.py --workload large
python3 benchmark/run_benchmark.py --reps 5 --out results
python3 benchmark/plot_results.py --results results

echo "### 7. Docker Stats and logs"
docker stats --no-stream edge-node fog-node cloud-node
docker logs --tail 10 edge-node
docker logs --tail 10 fog-node
docker logs --tail 10 cloud-node

echo "### 8. Stop:  docker compose down"
