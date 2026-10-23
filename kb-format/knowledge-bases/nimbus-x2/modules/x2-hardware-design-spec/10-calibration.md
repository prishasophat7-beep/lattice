---
id: x2-hardware-design-spec/10-calibration
module: x2-hardware-design-spec
title: "4.1 Calibration"
pages: [4, 4]
---

# 4.1 Calibration

_Manual p. 4_

Each unit is calibrated in a thermal chamber at 18 °C and 28 °C. The resulting offset and gain are written to the MCU option bytes. Because the display and MCU warm the enclosure by up to 1.8 °C, firmware applies a self-heating compensation model that uses backlight level and CPU load as inputs. After compensation, reported temperature must be within +/-0.3 °C of the chamber reference.
