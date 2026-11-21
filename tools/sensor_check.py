"""Bench test: print the raw voltage of every sensor twice a second.

Run this on the Pico (Thonny) before main.py to check the wiring, and to read
the voltages you need for calibrate_ph.py.
"""

import machine
import utime

SENSORS = {
    "pH in  (GP28)": machine.ADC(28),
    "pH out (GP27)": machine.ADC(27),
    "turb   (GP26)": machine.ADC(26),
}
VOLTS_PER_COUNT = 3.3 / 65535


def volts(adc, samples=10):
    return sum(adc.read_u16() for _ in range(samples)) / samples * VOLTS_PER_COUNT


while True:
    print("  |  ".join("%s %.3f V" % (name, volts(adc)) for name, adc in SENSORS.items()))
    utime.sleep(0.5)
