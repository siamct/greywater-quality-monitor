"""
Greywater reuse monitor: main program for the Raspberry Pi Pico W.

Sits at the outlet of a multi-stage greywater filter and answers one
question every 20 seconds: is the water coming out suitable for watering
plants? It reads pH, turbidity, TDS, temperature, the output of the UV
sterilising lamp and the flow rate, classifies the sample, logs it locally
and (if credentials are present) uploads it to a ThingSpeak dashboard.

LED: one blink per sample when PASS, two when CAUTION, steady on when FAIL.
"""

import time
from machine import I2C, Pin

import config
from ads1115 import ADS1115
from logger import CsvLogger
from quality import classify, describe
from sensors import AnalogueSensors, FlowMeter, TemperatureProbe

try:
    import secrets
except ImportError:
    secrets = None


def blink(led, times):
    for _ in range(times):
        led.on()
        time.sleep_ms(100)
        led.off()
        time.sleep_ms(150)


def main():
    led = Pin(config.LED_PIN, Pin.OUT)
    i2c = I2C(config.I2C_ID, sda=Pin(config.I2C_SDA_PIN), scl=Pin(config.I2C_SCL_PIN), freq=100_000)
    adc = ADS1115(i2c, config.ADS1115_ADDRESS)
    analogue = AnalogueSensors(adc)
    temperature = TemperatureProbe(config.DS18B20_PIN)
    flow = FlowMeter(config.FLOW_PIN)
    log = CsvLogger(config.LOG_FILE, config.LOG_MAX_BYTES)

    uploader = None
    if secrets is not None and secrets.THINGSPEAK_WRITE_KEY:
        from thingspeak import ThingSpeak, connect_wifi
        if connect_wifi(secrets.WIFI_SSID, secrets.WIFI_PASSWORD):
            uploader = ThingSpeak(secrets.THINGSPEAK_WRITE_KEY)
            print("Wi-Fi connected; uploading to ThingSpeak every %d s." % config.UPLOAD_PERIOD_S)
        else:
            print("Wi-Fi failed; logging locally only.")
    else:
        print("No secrets.py or no write key; logging locally only.")

    started = time.ticks_ms()
    last_upload = -config.UPLOAD_PERIOD_S

    while True:
        cycle_start = time.ticks_ms()
        uptime_s = time.ticks_diff(cycle_start, started) // 1000

        temp_c = temperature.read()
        sample = analogue.read(temp_c)
        sample["temp_c"] = temp_c
        sample["flow_lpm"] = flow.rate_lpm()
        sample["total_litres"] = flow.total_litres

        uv_ok = sample["uv_v"] >= config.UV_LAMP_MIN_V
        verdict, reasons = classify(sample["ph"], sample["turbidity_ntu"], sample["tds_ppm"], temp_c, uv_ok)

        print("pH %.2f  turb %.1f NTU  TDS %.0f ppm  %.1f C  UV %.2f V  flow %.2f L/min (%.1f L)  -> %s: %s %s" % (
            sample["ph"], sample["turbidity_ntu"], sample["tds_ppm"], temp_c, sample["uv_v"],
            sample["flow_lpm"], sample["total_litres"], verdict, describe(verdict),
            ("(" + ", ".join(reasons) + ")") if reasons else ""))

        log.write(uptime_s, sample, verdict, reasons)

        if uploader is not None and uptime_s - last_upload >= config.UPLOAD_PERIOD_S:
            if uploader.upload(sample, verdict):
                last_upload = uptime_s
            else:
                print("ThingSpeak upload failed (%d failures so far)" % uploader.failures)

        if verdict == "FAIL":
            led.on()
        else:
            led.off()
            blink(led, 1 if verdict == "PASS" else 2)

        elapsed = time.ticks_diff(time.ticks_ms(), cycle_start)
        remaining = config.SAMPLE_PERIOD_S * 1000 - elapsed
        if remaining > 0:
            time.sleep_ms(remaining)


main()
