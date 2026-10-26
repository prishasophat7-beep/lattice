---
id: interfaces-and-pinout/01-interfaces-and-pinout
module: interfaces-and-pinout
title: "5 Interfaces and Pinout"
pages: [4, 4]
---

# 5 Interfaces and Pinout

_Manual p. 4_

The main board connects to the relay board through a 10-pin 1.27 mm pitch board-to-board connector (J4). Revision C swapped pins 7 and 8 to simplify routing.

| Pin | Signal | Direction | Description |
| --- | --- | --- | --- |
| 1 | V5 | Out | 5 V supply to relay board |
| 2 | GND | - | Ground |
| 3 | RLY_W | Out | Heat relay drive |
| 4 | RLY_Y1 | Out | Cool stage 1 relay drive |
| 5 | RLY_Y2 | Out | Cool stage 2 relay drive |
| 6 | RLY_G | Out | Fan relay drive |
| 7 | RLY_OB | Out | Heat pump reversing valve |
| 8 | C_DET | In | C-wire present detect |
| 9 | PS_EN | Out | Power-stealing enable |
| 10 | GND | - | Ground |
