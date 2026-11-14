"""
Checks for the pure-logic parts of the firmware. Run on a PC:

    python -m pytest -q          (or)    python test_quality.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "firmware"))

from quality import PASS, CAUTION, FAIL, band, classify  # noqa: E402
from sensors import volts_to_ph, volts_to_ntu, volts_to_tds_ppm  # noqa: E402


def test_ph_calibration_points():
    assert abs(volts_to_ph(2.52) - 7.0) < 1e-9
    assert abs(volts_to_ph(3.05) - 4.0) < 1e-9
    assert volts_to_ph(2.30) > 7.0  # lower voltage means more alkaline


def test_turbidity_fit():
    assert abs(volts_to_ntu(4.20)) < 1e-9
    assert abs(volts_to_ntu(3.45) - 100.0) < 1e-9
    assert volts_to_ntu(4.50) == 0.0  # cannot be negative


def test_tds_temperature_compensation():
    warm = volts_to_tds_ppm(1.0, 30.0)
    cool = volts_to_tds_ppm(1.0, 20.0)
    assert cool > warm  # same voltage reads lower when the water is warmer


def test_bands():
    assert band("ph", 7.2) == PASS
    assert band("ph", 8.7) == CAUTION
    assert band("ph", 9.5) == FAIL
    assert band("turbidity_ntu", 3) == PASS
    assert band("turbidity_ntu", 7) == CAUTION
    assert band("turbidity_ntu", 12) == FAIL


def test_classify_clean_sample():
    verdict, reasons = classify(7.2, 2.0, 300, 20.0, uv_ok=True)
    assert verdict == PASS and reasons == []


def test_classify_uv_off_downgrades_pass():
    verdict, reasons = classify(7.2, 2.0, 300, 20.0, uv_ok=False)
    assert verdict == CAUTION
    assert any("UV" in r for r in reasons)


def test_classify_worst_wins():
    verdict, reasons = classify(8.7, 12.0, 300, 20.0, uv_ok=True)
    assert verdict == FAIL
    assert "turbidity fail" in reasons and "pH caution" in reasons


if __name__ == "__main__":
    failures = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok   ", name)
            except AssertionError as e:
                failures += 1
                print("FAIL ", name, e)
    sys.exit(1 if failures else 0)
