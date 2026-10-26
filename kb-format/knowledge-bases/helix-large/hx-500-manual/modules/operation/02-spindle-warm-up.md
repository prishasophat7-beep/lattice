---
id: operation/02-spindle-warm-up
module: operation
title: "5.2 Spindle warm-up"
pages: null
---

# 5.2 Spindle warm-up

_Manual pages not recorded_

Run program O9000 (warm-up) whenever the spindle has been stopped for more than 4 hours, and always at the start of each shift. The program runs the spindle for 5 minutes at 1,000 rpm, 5 minutes at 4,000 rpm and 5 minutes at 8,000 rpm (15 minutes total).
If the machine has been stopped for more than 72 hours, use the extended program O9001 instead (30 minutes, up to 12,000 rpm).
Rev B note: on Rev B machines the extended program is O9002 and runs up to 10,000 rpm.
