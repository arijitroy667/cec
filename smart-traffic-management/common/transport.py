"""
JSON over HTTP transport between the layers (Flask REST API + Requests).

Containers on one Docker host talk over a virtual bridge with ~0.1 ms latency,
which hides the real cost of reaching a remote Cloud.  To make the experiment
meaningful, every link has a *network profile* that emulates the WAN / LAN
characteristics from the slides:

    Edge <-> Fog   : few network hops,          10-50 ms   (slide 23)
    Edge <-> Cloud : multiple internet hops,   100-500 ms  (slide 23)

Two emulation modes are supported (env WAN_EMULATION):
  app  (default) - application-level emulation: propagation delay (RTT/2 each
                   way) + transmission delay (payload bits / link bandwidth)
                   are added around the real HTTP exchange.  Portable and
                   deterministic, works on every Docker host.
  tc             - kernel traffic control (tc netem) on the container's
                   interface, see edge_node/network_emulation.py.  Needs
                   NET_ADMIN and the sch_netem kernel module.
  off            - no emulation (raw Docker bridge).
"""
from __future__ import annotations

import json
import os
import threading
import time

import requests

_local = threading.local()


def _session():
    s = getattr(_local, "session", None)
    if s is None:
        s = _local.session = requests.Session()     # keep-alive connection per thread
    return s


class LinkProfile:
    """Network characteristics of one link (e.g. Edge -> Cloud WAN)."""

    def __init__(self, name, rtt_ms, bandwidth_mbit, mode=None):
        self.name = name
        self.rtt_ms = float(rtt_ms)
        self.bandwidth_mbit = float(bandwidth_mbit)
        self.mode = (mode or os.getenv("WAN_EMULATION", "app")).lower()

    def as_dict(self):
        return {"link": self.name, "rtt_ms": self.rtt_ms,
                "bandwidth_mbit": self.bandwidth_mbit, "emulation": self.mode}

    def one_way_delay(self, nbytes):
        if self.mode != "app":
            return 0.0
        tx = (nbytes * 8) / (self.bandwidth_mbit * 1e6) if self.bandwidth_mbit > 0 else 0.0
        return self.rtt_ms / 2000.0 + tx


def post_json(url, payload, link: LinkProfile | None = None, timeout=300):
    """POST `payload` as JSON and return (response_dict, transfer_metrics)."""
    t0 = time.perf_counter()
    body = json.dumps(payload, separators=(",", ":")).encode()
    t_ser = time.perf_counter()

    up_delay = link.one_way_delay(len(body)) if link else 0.0
    if up_delay:
        time.sleep(up_delay)                              # emulated uplink (Edge -> remote)
    t_net0 = time.perf_counter()
    resp = _session().post(url, data=body, headers={"Content-Type": "application/json"},
                           timeout=timeout)
    resp.raise_for_status()
    raw = resp.content
    t_net1 = time.perf_counter()
    down_delay = link.one_way_delay(len(raw)) if link else 0.0
    if down_delay:
        time.sleep(down_delay)                            # emulated downlink (remote -> Edge)
    data = json.loads(raw)
    t_end = time.perf_counter()
    return data, {
        "bytes_sent": len(body),
        "bytes_received": len(raw),
        "serialization_s": t_ser - t0,
        "http_round_trip_s": t_net1 - t_net0,          # includes remote processing
        "emulated_network_delay_s": up_delay + down_delay,
        "deserialization_s": t_end - t_net1 - down_delay,
        "total_s": t_end - t0,
    }


def get_json(url, link: LinkProfile | None = None, timeout=30):
    d = link.one_way_delay(200) if link else 0.0
    if d:
        time.sleep(d)
    r = _session().get(url, timeout=timeout)
    r.raise_for_status()
    if d:
        time.sleep(d)
    return r.json()
