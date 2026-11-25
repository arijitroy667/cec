"""
Charts for the performance evaluation (reads results/summary.json).

Usage:  python3 benchmark/plot_results.py --results results
Needs matplotlib on the Docker host:  pip install matplotlib
"""
from __future__ import annotations

import argparse
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ORDER = ("local", "full", "partial")
TITLE = {"local": "Local Execution", "full": "Full Offloading", "partial": "Partial Offloading"}
COLOR = {"local": "#257de0", "full": "#f47230", "partial": "#00b894"}   # fixed order, never cycled
INK, INK2, GRID = "#191919", "#666666", "#e6e6e6"
READINGS = {"small": 2_400, "medium": 24_000, "large": 96_000}

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "axes.edgecolor": INK2,
                     "axes.labelcolor": INK, "xtick.color": INK2, "ytick.color": INK2,
                     "axes.spines.top": False, "axes.spines.right": False})


def _style(ax):
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def _legend(fig):
    handles = [plt.Rectangle((0, 0), 1, 1, color=COLOR[s]) for s in ORDER]
    fig.legend(handles, [TITLE[s] for s in ORDER], loc="upper center", ncol=3, frameon=False,
               bbox_to_anchor=(0.5, 1.0))


def by_workload(s, metric, ylabel, path):
    wls = ["small", "medium", "large"]
    fig, axes = plt.subplots(1, 3, figsize=(9, 3.1))
    for ax, wl in zip(axes, wls):
        rows = {r["strategy"]: r for r in s["workload"] if r["workload"] == wl}
        vals = [rows[k][metric] for k in ORDER]
        sd_key = metric.replace("_mean_s", "_sd_s")
        errs = [rows[k].get(sd_key, 0) for k in ORDER]
        bars = ax.bar(range(3), vals, width=0.62, color=[COLOR[k] for k in ORDER], edgecolor="white",
                      linewidth=2, yerr=errs, error_kw={"ecolor": INK2, "elinewidth": 1, "capsize": 3})
        for b, v, e in zip(bars, vals, errs):
            ax.annotate(f"{v:.3f}", (b.get_x() + b.get_width() / 2, v + e), ha="center", va="bottom",
                        fontsize=8, color=INK, xytext=(0, 3), textcoords="offset points")
        ax.set_title(f"{wl} workload ({READINGS[wl]:,} readings)", fontsize=9, color=INK)
        ax.set_xticks(range(3), ["Local", "Full", "Partial"])
        ax.set_ylim(0, max(v + e for v, e in zip(vals, errs)) * 1.22)
        _style(ax)
    axes[0].set_ylabel(ylabel)
    _legend(fig)
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    fig.savefig(path, dpi=200)
    plt.close(fig)


def breakdown(s, path):
    wl = "large"
    rows = {r["strategy"]: r for r in s["workload"] if r["workload"] == wl}
    comps = [("edge_execution_mean_s", "Edge execution", "#4d4d4d"),
             ("cloud_execution_mean_s", "Cloud execution", "#9c9c9c"),
             ("communication_mean_s", "Communication (serialize + transfer + WAN)", "#d9d9d9")]
    fig, ax = plt.subplots(figsize=(7.5, 2.6))
    for i, k in enumerate(ORDER):
        left = 0.0
        for key, label, col in comps:
            v = rows[k][key]
            ax.barh(i, v, left=left, color=col, edgecolor="white", linewidth=2, height=0.6,
                    label=label if i == 0 else None)
            left += v
        ax.annotate(f"{left:.2f} s", (left, i), xytext=(4, 0), textcoords="offset points",
                    va="center", fontsize=8, color=INK)
    ax.set_yticks(range(3), [TITLE[k] for k in ORDER])
    ax.invert_yaxis()
    ax.set_xlabel("End-to-end response time, large workload (s)")
    ax.xaxis.grid(True, color=GRID)
    ax.set_axisbelow(True)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, 1.25), ncol=3, frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)


def network(s, path):
    net = s["network"]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(9, 3.1))
    for k in ORDER:
        pts = sorted((r["rtt_ms"], r["e2e_mean_s"]) for r in net if r["strategy"] == k and r["bandwidth_mbit"] == 50)
        a1.plot([p[0] for p in pts], [p[1] for p in pts], color=COLOR[k], linewidth=2, marker="o", markersize=6,
                markeredgecolor="white", markeredgewidth=1.5)
        pts = sorted((r["bandwidth_mbit"], r["e2e_mean_s"]) for r in net if r["strategy"] == k and r["rtt_ms"] == 100)
        a2.plot([p[0] for p in pts], [p[1] for p in pts], color=COLOR[k], linewidth=2, marker="o", markersize=6,
                markeredgecolor="white", markeredgewidth=1.5)
    a1.set_xlabel("Edge-Cloud round-trip time (ms), 50 Mbit/s")
    a2.set_xlabel("Edge-Cloud bandwidth (Mbit/s, log scale), RTT 100 ms")
    a2.set_xscale("log")
    a2.set_xticks([10, 50, 200], ["10", "50", "200"])
    for ax in (a1, a2):
        ax.set_ylabel("End-to-end response time (s)")
        ax.set_ylim(bottom=0)
        _style(ax)
    _legend(fig)
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    fig.savefig(path, dpi=200)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results")
    a = ap.parse_args()
    with open(os.path.join(a.results, "summary.json")) as f:
        s = json.load(f)
    by_workload(s, "e2e_mean_s", "End-to-end response time (s)", os.path.join(a.results, "chart_e2e_response_time.png"))
    by_workload(s, "task_execution_mean_s", "Task execution time (s)",
                os.path.join(a.results, "chart_task_execution_time.png"))
    breakdown(s, os.path.join(a.results, "chart_breakdown_large.png"))
    if s.get("network"):
        network(s, os.path.join(a.results, "chart_network_sweep.png"))
    print("charts written to", os.path.abspath(a.results))


if __name__ == "__main__":
    main()
