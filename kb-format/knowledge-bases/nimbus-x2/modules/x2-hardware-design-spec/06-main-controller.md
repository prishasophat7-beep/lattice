---
id: x2-hardware-design-spec/06-main-controller
module: x2-hardware-design-spec
title: "2.1 Main Controller"
pages: [2, 2]
---

# 2.1 Main Controller

_Manual p. 2_

The STM32U585 was selected for its TrustZone security, low-power modes and integrated graphics accelerator. It runs at 160 MHz with 2 MB of flash and 786 KB of SRAM. An external 16 MB QSPI flash stores UI assets and the secondary firmware slot for over-the-air updates.

- Core: Arm Cortex-M33 with TrustZone and FPU
- Clock: 160 MHz from a 16 MHz crystal plus 32.768 kHz RTC crystal
- Secure boot with keys stored in the OTP area
- Watchdog timeout: 4 seconds in normal operation
