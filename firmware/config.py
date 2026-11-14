"""
Pins, calibration and thresholds for the greywater monitor (Raspberry Pi Pico W).
"""

# ----- I2C bus for the ADS1115 analogue-to-digital converter -----
# The Pico only has three analogue inputs, and the pH probe wants better
# resolution than the on-board ADC gives, so all four analogue sensors go
# through a 16-bit ADS1115.
I2C_ID = 0
I2C_SDA_PIN = 4
I2C_SCL_PIN = 5
ADS1115_ADDRESS = 0x48

# ADS1115 channels
ADC_PH = 0
ADC_TURBIDITY = 1
ADC_TDS = 2
ADC_UV = 3

# ----- Digital sensors -----
DS18B20_PIN = 16          # waterproof temperature probe, 1-Wire
FLOW_PIN = 17             # YF-S201 hall-effect flow sensor, pulse output
UV_LAMP_RELAY_PIN = 18    # not driven by this code; read back to know whether the lamp is meant to be on

# ----- Calibration -----
# pH probe (analogue board, 5 V): two-point calibration with buffer solutions.
# volts measured in pH 7.00 and pH 4.00 buffers.
PH_CAL_7_V = 2.52
PH_CAL_4_V = 3.05

# Turbidity sensor (DFRobot SEN0189): linear fit from clear water (0 NTU)
# to a 100 NTU formazin standard, in volts.
TURBIDITY_CLEAR_V = 4.20
TURBIDITY_100NTU_V = 3.45

# TDS probe (Gravity TDS meter): the manufacturer's cubic fit with a
# temperature compensation coefficient of 2 % per degree from 25 C.
TDS_K = 1.0               # probe constant, 1.0 for a new probe

# UV sensor (GUVA-S12SD) at the sterilising lamp: volts when the lamp is
# healthy. Below UV_LAMP_MIN_V the lamp is weak or off.
UV_LAMP_MIN_V = 0.80

# Flow sensor: pulses per litre (YF-S201 datasheet gives 450).
FLOW_PULSES_PER_LITRE = 450

# ----- Sampling -----
SAMPLE_PERIOD_S = 20      # read all sensors
UPLOAD_PERIOD_S = 60      # ThingSpeak allows one update every 15 s on the free plan
ADC_AVERAGE_SAMPLES = 16  # readings averaged per channel to tame noise

# ----- Local log -----
LOG_FILE = "log.csv"
LOG_MAX_BYTES = 1_000_000

LED_PIN = "LED"
