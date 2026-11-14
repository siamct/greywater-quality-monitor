"""
Wi-Fi connection and ThingSpeak upload for the Pico W.

Field mapping on the ThingSpeak channel:
    field1 pH          field2 turbidity (NTU)   field3 TDS (ppm)   field4 temperature (C)
    field5 UV (V)      field6 flow (L/min)      field7 total litres  field8 verdict (0 pass, 1 caution, 2 fail)
"""

import time

import network
import urequests


def connect_wifi(ssid, password, timeout_s=20):
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(ssid, password)
        deadline = time.ticks_add(time.ticks_ms(), timeout_s * 1000)
        while not wlan.isconnected() and time.ticks_diff(deadline, time.ticks_ms()) > 0:
            time.sleep_ms(250)
    return wlan.isconnected()


class ThingSpeak:
    URL = "https://api.thingspeak.com/update"
    VERDICT_CODE = {"PASS": 0, "CAUTION": 1, "FAIL": 2}

    def __init__(self, write_key):
        self.write_key = write_key
        self.failures = 0

    @property
    def enabled(self):
        return bool(self.write_key)

    def upload(self, sample, verdict):
        if not self.enabled:
            return False
        fields = {
            "api_key": self.write_key,
            "field1": "%.2f" % sample["ph"],
            "field2": "%.1f" % sample["turbidity_ntu"],
            "field3": "%.0f" % sample["tds_ppm"],
            "field4": "%.1f" % sample["temp_c"],
            "field5": "%.2f" % sample["uv_v"],
            "field6": "%.2f" % sample["flow_lpm"],
            "field7": "%.1f" % sample["total_litres"],
            "field8": "%d" % self.VERDICT_CODE[verdict],
        }
        body = "&".join("%s=%s" % kv for kv in fields.items())
        try:
            r = urequests.post(self.URL, data=body,
                               headers={"Content-Type": "application/x-www-form-urlencoded"})
            ok = r.status_code == 200 and r.text.strip() != "0"
            r.close()
            if not ok:
                self.failures += 1
            return ok
        except Exception:
            self.failures += 1
            return False
