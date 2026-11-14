"""
Plot a monitor log with the reuse thresholds drawn as bands.

    python plot_log.py                    plots ../data/example_log.csv
    python plot_log.py path/to/log.csv    plots a log copied off the Pico

Writes docs/water_quality.png and docs/verdict.png and prints how much of
the run was PASS / CAUTION / FAIL and the most common reasons.
"""

import csv
import os
import sys
from collections import Counter

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "firmware"))
from quality import LIMITS  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LOG = os.path.join(HERE, "..", "data", "example_log.csv")
DOCS = os.path.join(HERE, "..", "docs")

VERDICT_COLOUR = {"PASS": "#2ca02c", "CAUTION": "#ff9f1c", "FAIL": "#d62728"}


def load(path):
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            rows.append({
                "t": int(r["uptime_s"]) / 60.0,
                "ph": float(r["ph"]),
                "turb": float(r["turbidity_ntu"]),
                "tds": float(r["tds_ppm"]),
                "temp": float(r["temp_c"]),
                "uv": float(r["uv_v"]),
                "flow": float(r["flow_lpm"]),
                "litres": float(r["total_litres"]),
                "verdict": r["verdict"],
                "reasons": r["reasons"],
            })
    return rows


def shade_limits(ax, name, ymin, ymax):
    lo, hi, lo_c, hi_c = LIMITS[name]
    a, b = (lo if lo is not None else ymin), (hi if hi is not None else ymax)
    ax.axhspan(a, b, color="#2ca02c", alpha=0.08)
    if hi is not None:
        ax.axhspan(hi, hi_c, color="#ff9f1c", alpha=0.10)
        ax.axhspan(hi_c, ymax, color="#d62728", alpha=0.08)
    if lo is not None:
        ax.axhspan(lo_c, lo, color="#ff9f1c", alpha=0.10)
        ax.axhspan(ymin, lo_c, color="#d62728", alpha=0.08)
    ax.set_ylim(ymin, ymax)


def plot(rows, note):
    os.makedirs(DOCS, exist_ok=True)
    t = [r["t"] for r in rows]

    fig, axes = plt.subplots(5, 1, figsize=(9, 9), sharex=True)
    axes[0].plot(t, [r["ph"] for r in rows], color="#1f77b4")
    shade_limits(axes[0], "ph", 5.5, 9.5)
    axes[0].set_ylabel("pH")

    axes[1].plot(t, [r["turb"] for r in rows], color="#1f77b4")
    shade_limits(axes[1], "turbidity_ntu", 0, 20)
    axes[1].set_ylabel("turbidity (NTU)")

    axes[2].plot(t, [r["tds"] for r in rows], color="#1f77b4")
    shade_limits(axes[2], "tds_ppm", 0, 700)
    axes[2].set_ylabel("TDS (ppm)")

    axes[3].plot(t, [r["uv"] for r in rows], color="#9467bd")
    axes[3].axhline(0.8, color="#d62728", linestyle="--", linewidth=1, label="lamp minimum")
    axes[3].set_ylabel("UV sensor (V)")
    axes[3].set_ylim(0, 1.5)
    axes[3].legend(fontsize=8, loc="lower right")

    axes[4].plot(t, [r["flow"] for r in rows], color="#17becf", label="flow (L/min)")
    axes[4].set_ylabel("flow (L/min)")
    ax2 = axes[4].twinx()
    ax2.plot(t, [r["litres"] for r in rows], color="#7f7f7f", linestyle=":", label="total (L)")
    ax2.set_ylabel("total (L)")
    axes[4].set_xlabel("time (minutes)")

    for ax in axes:
        ax.grid(alpha=0.3)
    fig.suptitle("Filter outlet readings with irrigation-reuse bands (green pass, amber caution, red fail)" + note, fontsize=10)
    fig.tight_layout()
    fig.savefig(os.path.join(DOCS, "water_quality.png"), dpi=130)
    plt.close(fig)

    # Verdict timeline.
    fig, ax = plt.subplots(figsize=(9, 1.9))
    for r in rows:
        ax.bar(r["t"], 1, width=20 / 60, color=VERDICT_COLOUR[r["verdict"]], align="edge", linewidth=0)
    ax.set_yticks([])
    ax.set_xlim(t[0], t[-1])
    ax.set_xlabel("time (minutes)")
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in VERDICT_COLOUR.values()]
    ax.legend(handles, list(VERDICT_COLOUR.keys()), loc="lower right", bbox_to_anchor=(1.0, 1.02), fontsize=8, ncol=3, frameon=False)
    ax.set_title("Verdict over the run" + note, fontsize=10, loc="left")
    fig.tight_layout()
    fig.savefig(os.path.join(DOCS, "verdict.png"), dpi=130)
    plt.close(fig)


def summarise(rows):
    counts = Counter(r["verdict"] for r in rows)
    n = len(rows)
    print("samples: %d over %.0f minutes, %.1f litres" % (n, rows[-1]["t"] - rows[0]["t"], rows[-1]["litres"]))
    for v in ("PASS", "CAUTION", "FAIL"):
        print("  %-8s %3.0f %%" % (v, 100 * counts[v] / n))
    reasons = Counter(x for r in rows for x in r["reasons"].split(";") if x)
    if reasons:
        print("most common reasons:")
        for reason, c in reasons.most_common(5):
            print("  %-40s %d samples" % (reason, c))


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_LOG
    rows = load(path)
    if not rows:
        print("no rows in", path)
        return
    note = "  (example data)" if os.path.abspath(path) == os.path.abspath(DEFAULT_LOG) else ""
    plot(rows, note)
    summarise(rows)
    print("plots written to", os.path.normpath(DOCS))


if __name__ == "__main__":
    main()
