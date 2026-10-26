---
id: sensors/01-sensors
module: sensors
title: "4 Sensors"
pages: [4, 4]
---

# 4 Sensors

_Manual p. 4_

| Sensor | Part | Interface | Accuracy | Sample rate |
| --- | --- | --- | --- | --- |
| Temperature / humidity | Sensirion SHT45 | I2C (0x44) | +/-0.1 °C, +/-1.0% RH | 1 Hz |
| CO2 | Sensirion SCD41 | I2C (0x62) | +/-(40 ppm + 5%) | 1 per 30 s |
| Ambient light | Vishay VEML7700 | I2C (0x10) | +/-10% | 2 Hz |
| Occupancy | Panasonic EKMC1603111 | GPIO | 5 m range | Event driven |
