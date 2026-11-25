# ---------------------------------------------------------------------------
# EDGE NODE - Edge Layer (Data Acquisition & Actuation)
# Build from the project root:  docker build -f edge_node/Dockerfile -t stms-edge-node .
# ---------------------------------------------------------------------------
FROM python:3.11-slim

LABEL layer="Edge Layer" role="Smart cameras / RSU gateway: vehicle counts & congestion in real time"

# iproute2 provides `tc` for the optional kernel-level WAN emulation (WAN_EMULATION=tc)
RUN apt-get update && apt-get install -y --no-install-recommends iproute2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY edge_node/requirements.txt /app/edge_node/requirements.txt
RUN pip install --no-cache-dir -r /app/edge_node/requirements.txt

COPY common/    /app/common/
COPY edge_node/ /app/edge_node/

ENV PYTHONUNBUFFERED=1 \
    NODE_NAME=edge-node \
    EDGE_PORT=5000

EXPOSE 5000
CMD ["python", "edge_node/edge_app.py"]
