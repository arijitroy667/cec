"""
Optional kernel-level WAN emulation with Linux traffic control (tc netem).

Used only when WAN_EMULATION=tc.  The Edge container needs the NET_ADMIN
capability (see docker-compose.yml) and the Docker host needs the sch_netem
kernel module (Ubuntu:  sudo modprobe sch_netem).

The delay is applied on the Edge container's interface that routes to the
Cloud Node (and, separately, to the Fog Node).  Because every packet leaving
the Edge on that interface is delayed, the round-trip time grows by the
configured value.
"""
from __future__ import annotations

import logging
import socket
import subprocess

log = logging.getLogger("edge.netem")


def _iface_to(host):
    ip = socket.gethostbyname(host)
    out = subprocess.run(["ip", "route", "get", ip], capture_output=True, text=True, check=True).stdout
    parts = out.split()
    return parts[parts.index("dev") + 1]


def apply_netem(host, rtt_ms, bandwidth_mbit):
    """Shape the Edge -> host link.  Returns a status dict (never raises)."""
    try:
        dev = _iface_to(host)
        cmd = ["tc", "qdisc", "replace", "dev", dev, "root", "netem",
               "delay", f"{int(rtt_ms)}ms", "rate", f"{bandwidth_mbit}mbit", "limit", "100000"]
        subprocess.run(cmd, capture_output=True, text=True, check=True)
        log.info("tc netem on %s (-> %s): delay %sms rate %smbit", dev, host, rtt_ms, bandwidth_mbit)
        return {"applied": True, "device": dev, "command": " ".join(cmd)}
    except Exception as exc:  # noqa: BLE001
        log.warning("tc netem not available (%s) - falling back to application-level emulation", exc)
        return {"applied": False, "error": str(exc)}
