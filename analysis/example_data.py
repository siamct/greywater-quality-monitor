"""
Write an example log that looks like a bench test of the filter, so the
plotting script can be demonstrated. This is generated data, not a
measurement: data/example_log.csv comes from this file.

Scenario (two hours, one sample every 20 s):
    0:00  tap water flushed through the rig (baseline)
    0:20  raw greywater (washing-up water with detergent) fed in; filter outlet improves over ~15 min
    1:05  UV lamp switched off for ten minutes to show the verdict drop to CAUTION
    1:30  a slug of soil washed in, turbidity spikes and the filter clears it
"""

import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "firmware"))
from quality import classify  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "example_log.csv")
UV_LAMP_MIN_V = 0.80


def ease(t, t0, t1):
    """0 before t0, 1 after t1, smooth in between."""
    if t <= t0:
        return 0.0
    if t >= t1:
        return 1.0
    x = (t - t0) / (t1 - t0)
    return x * x * (3 - 2 * x)


def main():
    rng = random.Random(7)
    rows = []
    total_litres = 0.0

    for t in range(0, 2 * 3600, 20):
        # Baseline tap water, then greywater arrives and the filter output settles.
        grey = ease(t, 20 * 60, 35 * 60)
        ph = 7.3 + 1.4 * grey - 0.6 * ease(t, 25 * 60, 50 * 60)            # detergent pushes pH up, then it settles
        turb = 0.8 + 9.0 * grey - 6.5 * ease(t, 22 * 60, 45 * 60)           # outlet turbidity after the filter clears
        tds = 180 + 320 * grey - 80 * ease(t, 30 * 60, 60 * 60)
        temp = 18.5 + 2.0 * grey

        # Soil slug at 1:30: turbidity spike that the filter clears over ten minutes.
        spike = ease(t, 90 * 60, 91 * 60) - ease(t, 92 * 60, 102 * 60)
        turb += 14.0 * spike
        tds += 60 * spike

        # UV lamp off between 1:05 and 1:15.
        uv_v = 1.25 if not (65 * 60 <= t < 75 * 60) else 0.05

        flow = 1.8 if t >= 10 * 60 else 0.0
        total_litres += flow * 20 / 60

        ph += rng.gauss(0, 0.03)
        turb = max(0.0, turb + rng.gauss(0, 0.15))
        tds += rng.gauss(0, 4)
        temp += rng.gauss(0, 0.05)
        uv_v += rng.gauss(0, 0.02)

        verdict, reasons = classify(ph, turb, tds, temp, uv_v >= UV_LAMP_MIN_V)
        rows.append((t, ph, turb, tds, temp, uv_v, flow, total_litres, verdict, ";".join(reasons)))

    with open(OUT, "w", newline="") as f:
        f.write("uptime_s,ph,turbidity_ntu,tds_ppm,temp_c,uv_v,flow_lpm,total_litres,verdict,reasons\n")
        for r in rows:
            f.write("%d,%.2f,%.1f,%.0f,%.1f,%.2f,%.2f,%.1f,%s,%s\n" % r)
    print("wrote %d rows to %s" % (len(rows), os.path.normpath(OUT)))


if __name__ == "__main__":
    main()
