"""
Minimal ADS1115 driver: single-ended reads, one channel at a time.

Only what this project needs. Gain is fixed at +/-6.144 V full scale so a
5 V sensor output cannot clip, which gives 0.1875 mV per bit.
"""

import time

_REG_CONVERSION = 0x00
_REG_CONFIG = 0x01

# Config register bits: start single conversion, single-ended mux, gain 6.144 V,
# single-shot mode, 128 samples/s, comparator disabled.
_CONFIG_BASE = 0x8000 | 0x0000 | 0x0100 | 0x0080 | 0x0003
_MUX_SINGLE = (0x4000, 0x5000, 0x6000, 0x7000)
_VOLTS_PER_BIT = 6.144 / 32768.0


class ADS1115:
    def __init__(self, i2c, address=0x48):
        self._i2c = i2c
        self._address = address
        self._buf = bytearray(2)

    def read_raw(self, channel):
        config = _CONFIG_BASE | _MUX_SINGLE[channel]
        self._buf[0] = (config >> 8) & 0xFF
        self._buf[1] = config & 0xFF
        self._i2c.writeto_mem(self._address, _REG_CONFIG, self._buf)
        time.sleep_ms(9)  # 128 SPS -> 7.8 ms per conversion
        data = self._i2c.readfrom_mem(self._address, _REG_CONVERSION, 2)
        value = (data[0] << 8) | data[1]
        if value & 0x8000:
            value -= 0x10000
        return value

    def read_volts(self, channel, samples=1):
        total = 0
        for _ in range(samples):
            total += self.read_raw(channel)
        return (total / samples) * _VOLTS_PER_BIT
