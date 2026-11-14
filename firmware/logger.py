"""
CSV log on the Pico's flash, used alongside (or instead of) ThingSpeak.
"""

import os


class CsvLogger:
    HEADER = "uptime_s,ph,turbidity_ntu,tds_ppm,temp_c,uv_v,flow_lpm,total_litres,verdict,reasons\n"

    def __init__(self, path, max_bytes):
        self.path = path
        self.max_bytes = max_bytes
        self.full = False
        try:
            os.stat(path)
        except OSError:
            with open(path, "w") as f:
                f.write(self.HEADER)

    def write(self, uptime_s, sample, verdict, reasons):
        if self.full:
            return False
        try:
            if os.stat(self.path)[6] > self.max_bytes:
                self.full = True
                return False
        except OSError:
            pass
        with open(self.path, "a") as f:
            f.write("%d,%.2f,%.1f,%.0f,%.1f,%.2f,%.2f,%.1f,%s,%s\n" % (
                uptime_s, sample["ph"], sample["turbidity_ntu"], sample["tds_ppm"], sample["temp_c"],
                sample["uv_v"], sample["flow_lpm"], sample["total_litres"], verdict, ";".join(reasons)))
        return True
