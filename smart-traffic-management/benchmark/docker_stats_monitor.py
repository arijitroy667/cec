"""
Docker Stats monitor (Performance Measurement: "Python time module and Docker Stats").

Runs `docker stats --no-stream` in a background thread on the Docker host and
labels every sample with the experiment phase that is currently running, so
CPU % and memory of edge-node / fog-node / cloud-node can be compared per
offloading strategy.

Standalone use:   python3 benchmark/docker_stats_monitor.py --seconds 30
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import threading
import time

CONTAINERS = ("edge-node", "fog-node", "cloud-node")


def _to_mb(s):
    s = s.strip()
    units = {"KiB": 1 / 1024, "MiB": 1, "GiB": 1024, "kB": 1 / 1000, "MB": 1, "GB": 1000, "B": 1 / 1e6}
    for u, f in units.items():
        if s.endswith(u):
            return float(s[: -len(u)]) * f
    return float(s or 0)


def docker_cmd():
    """Use `docker`, or `sudo docker` if the user is not in the docker group."""
    if shutil.which("docker") is None:
        return None
    probe = subprocess.run(["docker", "ps"], capture_output=True)
    return ["docker"] if probe.returncode == 0 else ["sudo", "-n", "docker"]


def sample_once(cmd):
    out = subprocess.run(cmd + ["stats", "--no-stream", "--format", "{{json .}}", *CONTAINERS],
                         capture_output=True, text=True, timeout=30).stdout
    rows = []
    for line in out.splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        rows.append({"container": d.get("Name"),
                     "cpu_pct": float(d.get("CPUPerc", "0%").rstrip("%") or 0),
                     "mem_mb": _to_mb(d.get("MemUsage", "0B / 0B").split("/")[0]),
                     "mem_pct": float(d.get("MemPerc", "0%").rstrip("%") or 0),
                     "net_io": d.get("NetIO"), "block_io": d.get("BlockIO"), "pids": d.get("PIDs")})
    return rows


class DockerStatsMonitor:
    def __init__(self):
        self.cmd = docker_cmd()
        self.phase = "idle"
        self.samples = []
        self._stop = threading.Event()
        self._t = None

    @property
    def available(self):
        return self.cmd is not None

    def start(self):
        if not self.available:
            print("[docker-stats] docker CLI not found - Docker Stats disabled")
            return self
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()
        return self

    def _run(self):
        while not self._stop.is_set():
            phase = self.phase
            try:
                for r in sample_once(self.cmd):
                    r.update(phase=phase, ts=time.time())
                    self.samples.append(r)
            except Exception as exc:  # noqa: BLE001
                print("[docker-stats] sample failed:", exc)
                time.sleep(2)

    def stop(self):
        self._stop.set()
        if self._t:
            self._t.join(timeout=35)

    def write_csv(self, path):
        if not self.samples:
            return
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(self.samples[0].keys()))
            w.writeheader()
            w.writerows(self.samples)

    def summary(self):
        agg = {}
        for s in self.samples:
            k = (s["phase"], s["container"])
            a = agg.setdefault(k, {"phase": s["phase"], "container": s["container"], "samples": 0,
                                   "cpu_pct_peak": 0.0, "cpu_pct_sum": 0.0, "mem_mb_peak": 0.0})
            a["samples"] += 1
            a["cpu_pct_peak"] = max(a["cpu_pct_peak"], s["cpu_pct"])
            a["cpu_pct_sum"] += s["cpu_pct"]
            a["mem_mb_peak"] = max(a["mem_mb_peak"], s["mem_mb"])
        out = []
        for a in agg.values():
            a["cpu_pct_mean"] = round(a.pop("cpu_pct_sum") / a["samples"], 2)
            a["cpu_pct_peak"] = round(a["cpu_pct_peak"], 2)
            a["mem_mb_peak"] = round(a["mem_mb_peak"], 1)
            out.append(a)
        return sorted(out, key=lambda x: (x["phase"], x["container"]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=20)
    ap.add_argument("--out", default="docker_stats.csv")
    a = ap.parse_args()
    m = DockerStatsMonitor().start()
    time.sleep(a.seconds)
    m.stop()
    m.write_csv(a.out)
    for row in m.summary():
        print(row)
