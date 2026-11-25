# ---------------------------------------------------------------------------
# CLOUD NODE - Cloud Layer (Centralized Intelligence) - Central Traffic Management System
# Build from the project root:  docker build -f cloud_node/Dockerfile -t stms-cloud-node .
# ---------------------------------------------------------------------------
FROM python:3.11-slim

LABEL layer="Cloud Layer" role="City-wide historical traffic patterns for long-term urban planning"

WORKDIR /app
COPY cloud_node/requirements.txt /app/cloud_node/requirements.txt
RUN pip install --no-cache-dir -r /app/cloud_node/requirements.txt

COPY common/     /app/common/
COPY cloud_node/ /app/cloud_node/

ENV PYTHONUNBUFFERED=1 \
    NODE_NAME=cloud-node \
    CLOUD_PORT=7000 \
    DATA_LAKE_PATH=/data/traffic_data_lake.jsonl

VOLUME ["/data"]
EXPOSE 7000
CMD ["python", "cloud_node/cloud_app.py"]
