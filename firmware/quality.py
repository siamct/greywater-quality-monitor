"""
Decides whether a sample of treated greywater is suitable for reuse.

The intended reuse is garden and plant irrigation, never drinking. The
thresholds follow the FAO irrigation water guidelines (Ayers and Westcot)
and the WHO greywater reuse guidance, simplified to pass / caution / fail
bands. Pure logic, shared by the firmware and the analysis scripts.
"""

# (lower_ok, upper_ok, lower_caution, upper_caution). None = no limit on that side.
LIMITS = {
    "ph":            (6.5, 8.4, 6.0, 9.0),
    "turbidity_ntu": (None, 5.0, None, 10.0),
    "tds_ppm":       (None, 450.0, None, 2000.0),
    "temp_c":        (5.0, 35.0, 2.0, 40.0),
}

PASS, CAUTION, FAIL = "PASS", "CAUTION", "FAIL"


def band(name, value):
    """Classify one measurement as PASS, CAUTION or FAIL."""
    lo, hi, lo_c, hi_c = LIMITS[name]
    if (lo is not None and value < lo_c) or (hi is not None and value > hi_c):
        return FAIL
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        return CAUTION
    return PASS


def classify(ph, turbidity_ntu, tds_ppm, temp_c, uv_ok=True):
    """
    Overall verdict for a sample.

    Returns (verdict, reasons) where verdict is PASS, CAUTION or FAIL and
    reasons is a list of short strings explaining anything that is not PASS.
    The UV sterilising stage must be working for a PASS: without it the water
    may still look clean but has not been disinfected.
    """
    results = {
        "ph": band("ph", ph),
        "turbidity_ntu": band("turbidity_ntu", turbidity_ntu),
        "tds_ppm": band("tds_ppm", tds_ppm),
        "temp_c": band("temp_c", temp_c),
    }
    reasons = []
    labels = {"ph": "pH", "turbidity_ntu": "turbidity", "tds_ppm": "TDS", "temp_c": "temperature"}
    for key, result in results.items():
        if result != PASS:
            reasons.append("%s %s" % (labels[key], result.lower()))

    worst = PASS
    if CAUTION in results.values():
        worst = CAUTION
    if FAIL in results.values():
        worst = FAIL

    if not uv_ok:
        reasons.append("UV lamp weak or off: not disinfected")
        if worst == PASS:
            worst = CAUTION

    return worst, reasons


def describe(verdict):
    return {
        PASS: "Suitable for irrigation (not for drinking)",
        CAUTION: "Use with care: dilute or check the filter",
        FAIL: "Not suitable for reuse",
    }[verdict]
