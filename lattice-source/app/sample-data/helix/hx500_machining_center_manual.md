# HX-500 Vertical Machining Center - Operator and Maintenance Manual (Rev C)

## 1. About this manual
This manual covers the HX-500 vertical machining center built from serial number HX5-2201 onwards (Rev C controller, firmware 4.x). Machines with serial numbers up to HX5-2200 use the Rev B controller; where Rev B behaves differently it is stated in a "Rev B note".

Read chapter 2 (Safety) before operating the machine. Only operators who have completed the Level 1 HX-500 training may run programs; only Level 2 technicians may perform the tasks marked (L2).

## 2. Safety

### 2.1 General safety rules
- Never open the enclosure door while the spindle is turning. The door interlock stops the spindle within 2 seconds; do not bypass it.
- Wear safety glasses at all times in the machining area. Gloves must not be worn while the spindle is running.
- Long hair must be tied back and loose clothing secured.
- Keep the floor area within 1 meter of the machine free of chips and coolant.

### 2.2 Emergency stop
Press the red EMERGENCY STOP button on the operator panel or on the side of the enclosure. All axes and the spindle stop and hydraulic pressure is released. To restart: remove the cause, twist the button to release it, press RESET, then re-home all axes (HOME ALL).

### 2.3 Lockout / tagout
Before any maintenance task marked (L2), and before opening the electrical cabinet, apply lockout/tagout as described in the Plant Safety Standard, section 4. Switch off the main isolator, lock it with your personal padlock, and wait 5 minutes for the spindle drive capacitors to discharge.

## 3. Specifications

| Item | Value |
|---|---|
| Table size | 1,100 x 500 mm |
| Max table load | 600 kg |
| Travel X / Y / Z | 850 / 510 / 560 mm |
| Spindle taper | BT40 |
| Spindle speed | 50 - 12,000 rpm |
| Spindle power | 15 kW (30 min rating), 11 kW continuous |
| Tool magazine | 24 tools |
| Max tool weight | 7 kg |
| Max tool length | 300 mm |
| Tool change time | 1.8 s (tool to tool) |
| Rapid traverse | 36 m/min |
| Positioning accuracy | +/- 0.005 mm |
| Repeatability | +/- 0.003 mm |
| Air supply | 6 bar minimum, 8 bar maximum, dry and filtered |
| Power supply | 400 V, 3 phase, 50 Hz, 35 kVA |
| Coolant tank | 250 liters |
| Machine weight | 5,800 kg |

Rev B note: Rev B machines have a 20-tool magazine and a 10,000 rpm spindle. All other values are the same.

## 4. Installation

### 4.1 Foundation
The machine must stand on a reinforced concrete floor at least 200 mm thick. Level the machine to within 0.02 mm/m using the six leveling bolts. Re-check the level after the first month of operation and then every 6 months.

### 4.2 Environment
Ambient temperature 10 - 35 °C, relative humidity below 75 % (non-condensing). For accuracy work (tolerances below 0.01 mm) the room temperature must be held within +/- 1 °C of 20 °C.

### 4.3 Electrical connection
The machine must be connected by a qualified electrician to a dedicated circuit protected by a 63 A slow-blow fuse. The protective earth resistance must not exceed 0.1 ohm.

## 5. Operation

### 5.1 Daily start-up
1. Check the air pressure gauge: 6 - 8 bar.
2. Check the way-lube oil level (sight glass at least half full).
3. Check the coolant level and concentration (see 7.4).
4. Switch on the main isolator and wait for the controller to boot (about 90 seconds).
5. Release the emergency stop and press RESET.
6. Press HOME ALL to home the axes.
7. Run the spindle warm-up program (see 5.2).

### 5.2 Spindle warm-up
Run program O9000 (warm-up) whenever the spindle has been stopped for more than 4 hours, and always at the start of each shift. The program runs the spindle for 5 minutes at 1,000 rpm, 5 minutes at 4,000 rpm and 5 minutes at 8,000 rpm (15 minutes total).
If the machine has been stopped for more than 72 hours, use the extended program O9001 instead (30 minutes, up to 12,000 rpm).
Rev B note: on Rev B machines the extended program is O9002 and runs up to 10,000 rpm.

### 5.3 Loading tools
Tools are loaded at the tool-change station with the spindle stopped. Each tool must be measured on the presetter and its length and diameter entered in the tool table before use. Tools heavier than 7 kg or longer than 300 mm must not be loaded. Adjacent pockets must be left empty for tools with a diameter above 80 mm (maximum 125 mm with empty adjacent pockets).

### 5.4 Running a program
Select the program, run it first in DRY RUN mode with the feed override at 25 % and single block on, then run the first part with the rapid override at 25 %. Only after the first part has been measured and approved may the program run in automatic mode at 100 % override.

### 5.5 Shutdown
At the end of the last shift: remove chips from the table, park the axes with program O9010, switch the coolant pump off, and switch off the main isolator. Do not switch off the main isolator while an alarm is active; record the alarm number first.

## 6. Alarms and error codes

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

### 6.1 Clearing a tool change timeout (A206)
1. Press RESET.
2. Switch to MDI mode and run M190 (tool changer recovery).
3. Check that the tool number on the screen matches the tool in the spindle.
4. If A206 occurs again within the same shift, stop and call a Level 2 technician.

### 6.2 Spindle over-temperature (A302)
Stop the cycle and let the spindle idle with the chiller running for 30 minutes. Check that the chiller filter is clean and the chiller tank is above the MIN mark. Do not restart production until the spindle temperature is below 45 °C. If A302 occurs twice in one week, a Level 2 technician must check the spindle bearings.

## 7. Maintenance

### 7.1 Maintenance schedule

| Interval | Task | Done by |
|---|---|---|
| Daily | Clean chips from table and enclosure; check air, lube and coolant levels | Operator |
| Weekly | Clean the coolant tank strainer; check the way covers for damage; drain the air filter water | Operator |
| Monthly | Clean the chiller filter; check the tool changer gripper; check the Z counterbalance pressure | Operator |
| Every 500 hours | Replace the air filter element; check the spindle drawbar force (L2) | Level 2 technician |
| Every 2,000 hours | Replace the hydraulic oil and filter; check the ballscrew backlash (L2) | Level 2 technician |
| Every 4,000 hours or 2 years | Replace the coolant completely and clean the tank; replace the controller battery (L2) | Level 2 technician |
| Every 8,000 hours | Spindle bearing inspection by the manufacturer's service | Helix service |

Whichever comes first applies where both hours and years are given.

### 7.2 Lubrication

| Point | Lubricant | Quantity | Interval |
|---|---|---|---|
| Way-lube tank | ISO VG 68 slideway oil | 2 liters | Refill when below half |
| Hydraulic unit | ISO VG 32 hydraulic oil | 12 liters | Replace every 2,000 hours |
| Tool changer cam | NLGI 2 lithium grease | 10 g | Every 1,000 hours |
| Spindle | Sealed for life | - | Do not lubricate |

### 7.3 Drawbar force check (L2)
The drawbar clamping force must be between 12 and 14 kN for BT40 tools. Measure with the drawbar force gauge. If the force is below 12 kN, replace the disc spring stack. Never run the machine with a clamping force below 10 kN.

### 7.4 Coolant
Use water-miscible coolant at 6 - 8 % concentration (refractometer reading x 1.0 for Helix Cool 300). Below 5 % there is a risk of corrosion and bacterial growth; above 10 % the coolant foams and can irritate the skin. Check the concentration daily and the pH weekly (target pH 8.5 - 9.5).

### 7.5 Ballscrew backlash (L2)
Measure the backlash on each axis with a dial indicator. The limit is 0.010 mm. Between 0.010 and 0.020 mm, enter backlash compensation in parameter group 1850. Above 0.020 mm the ballscrew must be replaced.

## 8. Torque values

| Fastener | Size | Torque |
|---|---|---|
| Leveling bolt lock nut | M24 | 200 Nm |
| Vise to table | M16 | 120 Nm |
| Fixture to table | M12 | 70 Nm |
| Tool holder pull stud | M16 | 100 Nm (BT40) |
| Way cover screws | M6 | 10 Nm |

## 9. Warranty
The machine is covered for 24 months from installation or 6,000 spindle hours, whichever comes first. The warranty does not cover damage from crashes, use of non-approved coolant, or maintenance not carried out according to chapter 7. Spindle bearings are covered for 12 months only if the warm-up rules in 5.2 have been followed (the controller logs warm-up runs).
