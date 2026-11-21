"""Greywater filtration monitor (MicroPython, Raspberry Pi Pico).

Reads intake pH, outtake pH and intake turbidity, decides whether the filtered
water is safe to reuse, drives the outlet valve (servo), a status LED, a buzzer
and a 128x64 OLED, and streams readings to the desktop dashboard over USB.

Serial out (one line per loop, comma separated):
    ph_in, ph_out, turb_in, turb_out_est, valve_open, safe, v_ph_in, v_ph_out, v_turb
Serial in:
    LOCK / UNLOCK      same as pressing the lock button
"""

import select
import sys

import machine
import utime

try:                                    # real screen is an SH1106, Wokwi only has SSD1306
    import sh1106 as oled_driver
    OLED_CLASS = oled_driver.SH1106_I2C
except ImportError:
    import ssd1306 as oled_driver
    OLED_CLASS = oled_driver.SSD1306_I2C

# --- Pins ---------------------------------------------------------------
TURB_IN_PIN = 26
PH_OUT_PIN = 27
PH_IN_PIN = 28
BUTTON_PIN = 21                         # to 3V3, pulled down
SAFE_LED_PIN = 20
LOCK_LED_PIN = 19
BUZZER_PIN = 18
SERVO_PIN = 22
I2C_SDA, I2C_SCL = 4, 5

# --- Calibration --------------------------------------------------------
# pH = slope * volts + offset, from buffer-solution readings (see tools/calibrate_ph.py).
# The probes drift, so re-run the calibration before trusting these.
PH_IN_CAL = (8.8031, -10.4569)
PH_OUT_CAL = (8.8031, -12.3569)
TURB_CLEAR_V = 3.3                      # sensor voltage in clear water  -> 0 NTU
TURB_MUDDY_V = 2.5                      # sensor voltage treated as      -> 100 NTU
FILTER_PASS = 0.15                      # no outlet turbidity sensor: assume 85 % removed

# --- Safety rule ----------------------------------------------------------
PH_SAFE = (6.9, 7.8)
TURB_SAFE_MAX = 10.0                    # NTU

# --- Valve servo ------------------------------------------------------------
SERVO_CLOSED = 2500
SERVO_OPEN = 4000

LOOP_MS = 100
DEBOUNCE_MS = 250
UNSAFE_REMINDER_MS = 15000            # keep nagging while the water is bad
SAMPLES = 10
VOLTS_PER_COUNT = 3.3 / 65535

# --- Hardware -----------------------------------------------------------
adc_turb_in = machine.ADC(TURB_IN_PIN)
adc_ph_out = machine.ADC(PH_OUT_PIN)
adc_ph_in = machine.ADC(PH_IN_PIN)

button = machine.Pin(BUTTON_PIN, machine.Pin.IN, machine.Pin.PULL_DOWN)
safe_led = machine.Pin(SAFE_LED_PIN, machine.Pin.OUT)
lock_led = machine.Pin(LOCK_LED_PIN, machine.Pin.OUT)
buzzer = machine.Pin(BUZZER_PIN, machine.Pin.OUT)

servo = machine.PWM(machine.Pin(SERVO_PIN))
servo.freq(50)

i2c = machine.I2C(0, sda=machine.Pin(I2C_SDA), scl=machine.Pin(I2C_SCL), freq=400000)
oled = OLED_CLASS(128, 64, i2c)


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def read_volts(adc):
    """Average a few samples to knock down ADC noise."""
    return sum(adc.read_u16() for _ in range(SAMPLES)) / SAMPLES * VOLTS_PER_COUNT


def to_ph(volts, cal):
    slope, offset = cal
    return clamp(slope * volts + offset, 0.0, 14.0)


def to_ntu(volts):
    """Turbidity sensors read lower voltages in cloudier water."""
    if volts >= TURB_CLEAR_V:
        return 0.0
    return clamp((TURB_CLEAR_V - volts) * 100.0 / (TURB_CLEAR_V - TURB_MUDDY_V), 0.0, 100.0)


def read_sensors():
    v_ph_in, v_ph_out, v_turb = read_volts(adc_ph_in), read_volts(adc_ph_out), read_volts(adc_turb_in)
    turb_in = to_ntu(v_turb)
    return {
        "ph_in": to_ph(v_ph_in, PH_IN_CAL),
        "ph_out": to_ph(v_ph_out, PH_OUT_CAL),
        "turb_in": turb_in,
        "turb_out": max(1.0, turb_in * FILTER_PASS),     # estimate, see FILTER_PASS
        "v_ph_in": v_ph_in, "v_ph_out": v_ph_out, "v_turb": v_turb,
    }


def is_safe(r):
    return PH_SAFE[0] <= r["ph_out"] <= PH_SAFE[1] and r["turb_out"] <= TURB_SAFE_MAX


def draw(r, safe, valve_open, locked):
    oled.fill(0)
    oled.text("FILTRATION SYS", 5, 0)
    oled.hline(0, 10, 128, 1)

    # pH bar, 5.0 - 9.0, with the safe band marked underneath
    oled.text("pH: %.2f" % r["ph_out"], 0, 15)
    oled.rect(0, 25, 100, 5, 1)
    lo = int((PH_SAFE[0] - 5.0) / 4.0 * 100)
    hi = int((PH_SAFE[1] - 5.0) / 4.0 * 100)
    oled.hline(lo, 31, hi - lo, 1)
    oled.vline(lo, 30, 3, 1)
    oled.vline(hi, 30, 3, 1)
    oled.fill_rect(int((clamp(r["ph_out"], 5.0, 9.0) - 5.0) / 4.0 * 100), 23, 2, 9, 1)
    oled.text("V" if valve_open else ("L" if locked else "X"), 118, 15)

    oled.text("Turb: %.1f NTU" % r["turb_out"], 0, 37)

    if safe:
        oled.fill_rect(0, 50, 128, 14, 1)
        oled.text("STATUS: SAFE", 15, 53, 0)
    else:
        oled.text("STATUS: UNSAFE", 10, 53, 1)
    oled.show()


class Buzzer:
    """Non-blocking beeper. A pattern is a list of (on_ms, off_ms) pairs that
    gets played out a step at a time from the main loop, so beeping never
    stalls the sensor readings or the button (the old version used sleep_ms).

    What it does:
        water goes unsafe      3 short beeps
        still unsafe           1 reminder beep every UNSAFE_REMINDER_MS
        water back in range    1 short chirp
        button / LOCK command  10 ms click so you know it registered
    """

    ALARM = [(80, 80), (80, 80), (80, 0)]
    REMINDER = [(150, 0)]
    RECOVERED = [(30, 0)]
    CLICK = [(10, 0)]

    def __init__(self, pin):
        self.pin = pin
        self.steps = []
        self.step_end = 0
        self.on = False

    def play(self, pattern):
        self.steps = list(pattern)
        self.step_end = 0
        self.on = False

    def update(self, now):
        if utime.ticks_diff(now, self.step_end) < 0:
            return                                  # current on/off step still running
        if self.on:                                 # finished an "on" -> do its "off" gap
            self.on = False
            self.pin.value(0)
            self.step_end = utime.ticks_add(now, self.steps.pop(0)[1])
        elif self.steps:                            # start the next beep
            self.on = True
            self.pin.value(1)
            self.step_end = utime.ticks_add(now, self.steps[0][0])


class Monitor:
    def __init__(self):
        self.locked = True              # user lock: starts closed
        self.was_safe = True
        self.valve_open = False
        self.last_press = 0
        self.button_was_down = False
        self.buzzer = Buzzer(buzzer)
        self.last_reminder = 0
        self.poll = select.poll()
        self.poll.register(sys.stdin, select.POLLIN)
        self.set_valve(False)
        lock_led.value(1)

    def set_valve(self, open_):
        self.valve_open = open_
        servo.duty_u16(SERVO_OPEN if open_ else SERVO_CLOSED)

    def set_locked(self, locked):
        self.locked = locked
        lock_led.value(1 if locked else 0)
        self.buzzer.play(Buzzer.CLICK)
        print("Action: " + ("LOCKED" if locked else "UNLOCKED"))

    def check_button(self):
        down = button.value() == 1
        now = utime.ticks_ms()
        if down and not self.button_was_down and utime.ticks_diff(now, self.last_press) > DEBOUNCE_MS:
            self.last_press = now
            self.set_locked(not self.locked)
        self.button_was_down = down

    def check_serial(self):
        while self.poll.poll(0):
            cmd = sys.stdin.readline().strip().upper()
            if cmd == "LOCK":
                self.set_locked(True)
            elif cmd == "UNLOCK":
                self.set_locked(False)

    def update(self):
        self.check_button()
        self.check_serial()

        now = utime.ticks_ms()
        r = read_sensors()
        safe = is_safe(r)
        if self.was_safe and not safe:
            print("ALERT: unsafe water, valve closed")
            self.buzzer.play(Buzzer.ALARM)
            self.last_reminder = now
        elif not safe and utime.ticks_diff(now, self.last_reminder) > UNSAFE_REMINDER_MS:
            print("ALERT: still unsafe")
            self.buzzer.play(Buzzer.REMINDER)
            self.last_reminder = now
        elif safe and not self.was_safe:
            print("OK: water back in range")
            self.buzzer.play(Buzzer.RECOVERED)
        self.was_safe = safe
        self.buzzer.update(now)

        # Valve opens only when the user has unlocked it AND the water is safe.
        self.set_valve(safe and not self.locked)
        safe_led.value(1 if safe else 0)
        draw(r, safe, self.valve_open, self.locked)

        print("%.2f,%.2f,%.1f,%.1f,%d,%d,%.3f,%.3f,%.3f" % (
            r["ph_in"], r["ph_out"], r["turb_in"], r["turb_out"],
            self.valve_open, safe, r["v_ph_in"], r["v_ph_out"], r["v_turb"]))


if __name__ == "__main__":
    print("Online")
    monitor = Monitor()
    while True:
        monitor.update()
        utime.sleep_ms(LOOP_MS)
