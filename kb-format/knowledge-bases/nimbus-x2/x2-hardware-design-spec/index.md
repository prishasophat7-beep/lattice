# X2 hardware design spec

Source file: `x2_hardware_design_spec.pdf` · 5 pages

Introduces the Nimbus X2 smart thermostat hardware, its target market and the scope and revision history of this specification. Describes the X2 architecture built around an STM32U585 MCU connected to the radio, sensors, display, relay board and power supply. Explains how X2 is powered from 24 VAC, including power stealing for installations without a C wire, rail specifications and measured current draw.

| Module | Pages | Summary |
| --- | --- | --- |
| [Overview](modules/overview/01-overview.md) | p. 1 | Introduces the Nimbus X2 smart thermostat hardware, its target market and the scope and revision history of this specification. |
| [System Architecture](modules/system-architecture/01-system-architecture.md) | pp. 1–2 | Describes the X2 architecture built around an STM32U585 MCU connected to the radio, sensors, display, relay board and power supply. |
| [Power Subsystem](modules/power-subsystem/01-power-subsystem.md) | p. 3 | Explains how X2 is powered from 24 VAC, including power stealing for installations without a C wire, rail specifications and measured current draw. |
| [Sensors](modules/sensors/01-sensors.md) | p. 4 | Lists the temperature, humidity, CO2 and ambient light sensors used and their accuracy, then describes the factory calibration procedure. |
| [Interfaces and Pinout](modules/interfaces-and-pinout/01-interfaces-and-pinout.md) | p. 4 | Gives the pin assignments for the 10-pin main-to-relay-board connector. |
| [Environmental and Compliance](modules/environmental-and-compliance/01-environmental-and-compliance.md) | p. 4 | Summarizes operating conditions and the regulatory certifications X2 must pass before launch. |

## Sections

**Overview**
- [1 Overview](modules/overview/01-overview.md) — p. 1
- [1.1 Scope](modules/overview/02-scope.md) — p. 1
- [1.2 Revision History](modules/overview/03-revision-history.md) — p. 1

**System Architecture**
- [2 System Architecture](modules/system-architecture/01-system-architecture.md) — pp. 1–2
- [2.1 Main Controller](modules/system-architecture/02-main-controller.md) — p. 2
- [2.2 Wireless Connectivity](modules/system-architecture/03-wireless-connectivity.md) — p. 2

**Power Subsystem**
- [3 Power Subsystem](modules/power-subsystem/01-power-subsystem.md) — p. 3

**Sensors**
- [4 Sensors](modules/sensors/01-sensors.md) — p. 4
- [4.1 Calibration](modules/sensors/02-calibration.md) — p. 4

**Interfaces and Pinout**
- [5 Interfaces and Pinout](modules/interfaces-and-pinout/01-interfaces-and-pinout.md) — p. 4

**Environmental and Compliance**
- [6 Environmental and Compliance](modules/environmental-and-compliance/01-environmental-and-compliance.md) — p. 4
