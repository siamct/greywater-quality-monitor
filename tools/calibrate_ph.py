"""Work out pH calibration constants from buffer-solution readings.

Put the probe in two or more buffers of known pH, note the voltage that
sensor_check.py shows for each, and pass them in as pH=volts pairs:

    python tools/calibrate_ph.py 6.86=2.12 7.2=2.33 9.18=2.38

It fits a straight line (least squares) and prints the (slope, offset) pair to
paste into PH_IN_CAL or PH_OUT_CAL in firmware/main.py, plus how far each
buffer is from the fitted line. Runs on a normal PC, not the Pico.
"""

import sys


def fit(points):
    n = len(points)
    mean_v = sum(v for _, v in points) / n
    mean_p = sum(p for p, _ in points) / n
    sxx = sum((v - mean_v) ** 2 for _, v in points)
    if sxx == 0:
        raise ValueError("all the voltages are the same, so no line can be fitted")
    slope = sum((v - mean_v) * (p - mean_p) for p, v in points) / sxx
    return slope, mean_p - slope * mean_v


def main(args):
    if len(args) < 2:
        print(__doc__)
        return 1
    points = []
    for arg in args:
        ph, volts = arg.split("=")
        points.append((float(ph), float(volts)))
    slope, offset = fit(points)
    print("CAL = (%.4f, %.4f)" % (slope, offset))
    for ph, volts in points:
        print("  buffer %5.2f  at %.3f V  ->  reads %5.2f" % (ph, volts, slope * volts + offset))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
