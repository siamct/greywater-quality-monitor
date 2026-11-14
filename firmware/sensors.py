"""
Converts raw voltages and pulses into water-quality numbers.

The conversion formulas are kept in plain functions so they can be unit
tested on a PC without the hardware; the classes below just wire them to
the ADS1115, the DS18B20 and the flow sensor.
"""

import time

import config


# ----------------------------------------------------------------- conversions

def volts_to_ph(v, cal_7_v=config.PH_CAL_7_V, cal_4_v=config.PH_CAL_4_V):
    """Two-point linear calibration. The probe output falls as pH rises."""
    slope = (7.0 - 4.0) / (cal_7_v - cal_4_v)      # pH per volt (negative)
    return 7.0 + (v - cal_7_v) * slope


def volts_to_ntu(v, clear_v=config.TURBIDITY_CLEAR_V, ntu100_v=config.TURBIDITY_100NTU_V):
    """Linear fit between clear water and a 100 NTU standard. Output falls as turbidity rises."""
    ntu = (clear_v - v) / (clear_v - ntu100_v) * 100.0
    return max(0.0, ntu)


def volts_to_tds_ppm(v, temp_c, k=config.TDS_K):
    """
    Gravity TDS meter formula: compensate the voltage to 25 C, then apply
    the manufacturer's cubic fit. Result in ppm (mg/L).
    """
    compensation = 1.0 + 0.02 * (temp_c - 25.0)
    vc = v / compensation
    return k * (133.42 * vc ** 3 - 255.86 * vc ** 2 + 857.39 * vc)


def pulses_to_litres(pulses, pulses_per_litre=config.FLOW_PULSES_PER_LITRE):
    return pulses / pulses_per_litre


# ------------------------------------------------------------------- hardware

class AnalogueSensors:
    """pH, turbidity, TDS and UV through the ADS1115."""

    def __init__(self, adc):
        self._adc = adc

    def read(self, temp_c):
        n = config.ADC_AVERAGE_SAMPLES
        v_ph = self._adc.read_volts(config.ADC_PH, n)
        v_turb = self._adc.read_volts(config.ADC_TURBIDITY, n)
        v_tds = self._adc.read_volts(config.ADC_TDS, n)
        v_uv = self._adc.read_volts(config.ADC_UV, n)
        return {
            "ph": volts_to_ph(v_ph),
            "turbidity_ntu": volts_to_ntu(v_turb),
            "tds_ppm": volts_to_tds_ppm(v_tds, temp_c),
            "uv_v": v_uv,
            "raw_v": (v_ph, v_turb, v_tds, v_uv),
        }


class TemperatureProbe:
    """Waterproof DS18B20 on 1-Wire."""

    def __init__(self, pin):
        import onewire
        import ds18x20
        from machine import Pin

        self._bus = ds18x20.DS18X20(onewire.OneWire(Pin(pin)))
        roms = self._bus.scan()
        self._rom = roms[0] if roms else None
        self.last_c = 20.0

    def read(self):
        if self._rom is None:
            return self.last_c
        try:
            self._bus.convert_temp()
            time.sleep_ms(750)
            self.last_c = self._bus.read_temp(self._rom)
        except Exception:
            pass
        return self.last_c


class FlowMeter:
    """Counts pulses from a YF-S201 in an interrupt; reports litres and L/min."""

    def __init__(self, pin):
        from machine import Pin

        self._pulses = 0
        self._last_pulses = 0
        self._last_time = time.ticks_ms()
        self._pin = Pin(pin, Pin.IN, Pin.PULL_UP)
        self._pin.irq(trigger=Pin.IRQ_FALLING, handler=self._on_pulse)

    def _on_pulse(self, _pin):
        self._pulses += 1

    @property
    def total_litres(self):
        return pulses_to_litres(self._pulses)

    def rate_lpm(self):
        """Average flow rate since the last call, in litres per minute."""
        now = time.ticks_ms()
        pulses = self._pulses
        dt_min = time.ticks_diff(now, self._last_time) / 60000.0
        delta = pulses - self._last_pulses
        self._last_pulses = pulses
        self._last_time = now
        if dt_min <= 0:
            return 0.0
        return pulses_to_litres(delta) / dt_min
