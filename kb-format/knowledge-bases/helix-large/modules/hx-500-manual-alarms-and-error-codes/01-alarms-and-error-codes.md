---
id: hx-500-manual-alarms-and-error-codes/01-alarms-and-error-codes
module: hx-500-manual-alarms-and-error-codes
title: "6. Alarms and error codes"
pages: null
---

# 6. Alarms and error codes

_Manual pages not recorded_

| Code | Meaning | Operator action | Escalate to |
|---|---|---|---|
| A101 | Air pressure low (below 6 bar) | Check the compressor and the air filter; machine stops at the end of the current block | Maintenance if not cleared in 15 min |
| A102 | Way-lube oil level low | Refill way-lube oil (ISO VG 68) | - |
| A103 | Coolant level low | Top up coolant | - |
| A110 | Door open during cycle | Close the door and press CYCLE START | - |
| A205 | Tool not clamped | Do not restart; check the pull stud and the tool holder | Level 2 technician |
| A206 | Tool change timeout | Press RESET, run the tool changer recovery (M-code M190) | Level 2 technician if it repeats |
| A210 | Tool length exceeds limit | Check the tool table entry against the presetter | - |
| A301 | Spindle overload | Reduce the feed or depth of cut; check the tool for wear | Maintenance if it repeats 3 times in a shift |
| A302 | Spindle over-temperature (above 70 °C) | Stop the cycle; let the spindle cool for 30 minutes; check the chiller | Maintenance |
| A303 | Spindle vibration high | Stop; check the tool balance and the tool holder | Level 2 technician |
| A401 | X axis following error | Press RESET; re-home; if it repeats stop the machine | Maintenance |
| A402 | Y axis following error | As A401 | Maintenance |
| A403 | Z axis following error | As A401; check the Z counterbalance pressure (55 - 60 bar) | Maintenance |
| A501 | Hydraulic pressure low | Stop; check the hydraulic oil level and the pump | Maintenance |
| A601 | Chiller fault | Stop the spindle; check the chiller power and the filter | Maintenance |
| E900 | Controller battery low | Replace the controller battery within 7 days with power ON (L2) | Level 2 technician |
| E901 | Absolute encoder battery low | Replace the encoder batteries within 3 days with power ON, otherwise all axis positions are lost (L2) | Level 2 technician |
| E950 | Controller communication error | Switch off, wait 1 minute, switch on | IT / Controls engineer |

Rev B note: on Rev B machines alarm A302 is triggered above 65 °C instead of 70 °C, and alarm A303 does not exist.
