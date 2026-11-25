# ---------------------------------------------------------------------------
# FOG NODE - Fog Layer (Distributed Intelligence)
# Build from the project root:  docker build -f fog_node/Dockerfile -t stms-fog-node .
# ---------------------------------------------------------------------------
FROM python:3.11-slim

LABEL layer="Fog Layer" role="Coordinates signal timings across 10 adjacent intersections"

WORKDIR /app
COPY fog_node/requirements.txt /app/fog_node/requirements.txt
RUN pip install --no-cache-dir -r /app/fog_node/requirements.txt

COPY common/   /app/common/
COPY fog_node/ /app/fog_node/

ENV PYTHONUNBUFFERED=1 \
    NODE_NAME=fog-node \
    FOG_PORT=6000

EXPOSE 6000
CMD ["python", "fog_node/fog_app.py"]
