"""Large documents for testing (written as Markdown, then exported with make_web_bundle.py):

    python eval/rag_eval/make_large_kb.py OUT_DIR

- HX-900 service manual: 40 chapters x 6 sections (~150 pages), facts buried deep in it, and one
  very long section (chapter 22.6, ~40,000 characters) whose key fact is near its end
- HX-900 spare parts catalogue: one complete table of 500 parts
- HX-900 spindle log export: 2,000 rows given only in part, with the platform's note on the whole file
The facts the questions in questions_large.json ask for are fixed; the text around them is generated
(seeded, so the same every run)."""
from __future__ import annotations

import random
import sys
from pathlib import Path

R = random.Random(900)
TOPICS = ["axis drive", "spindle cartridge", "coolant pump", "tool changer", "way covers", "lubrication unit",
          "hydraulic clamp", "chip conveyor", "probe system", "electrical cabinet", "control panel",
          "pneumatic unit", "door interlock", "rotary table", "ballscrew", "linear guide", "encoder",
          "servo amplifier", "chiller", "mist collector"]
VERBS = ["inspect", "clean", "check", "adjust", "replace", "record", "verify", "tighten", "measure", "lubricate"]
PARTS = ["seal", "bearing", "filter", "hose", "sensor", "fuse", "relay", "gasket", "belt", "coupling", "valve",
         "bracket", "cable", "switch", "nozzle"]
SUPPLIERS = ["Kirloskar", "SKF India", "Festo", "Bosch Rexroth", "Siemens", "Parker", "Schaeffler"]


def sentence(topic: str) -> str:
    v, p = R.choice(VERBS), R.choice(PARTS)
    n = R.randint(2, 48)
    forms = [f"Every {n * 50} hours, {v} the {topic} {p} and note the result in the machine log.",
             f"The {topic} {p} must be {v}ed by a Level {R.randint(1, 2)} technician only.",
             f"If the {topic} shows wear above {R.randint(1, 9)}0 %, {v} the {p} before the next shift.",
             f"Use only Helix-approved {p}s on the {topic}; others void the service agreement.",
             f"After any work on the {topic}, run the self-test from the maintenance menu (about {n} seconds).",
             f"A loose {p} on the {topic} causes vibration and must be {v}ed within {n} hours."]
    return R.choice(forms)


def paragraph(topic: str, n: int = 6) -> str:
    return " ".join(sentence(topic) for _ in range(n))


# facts the questions ask for: (chapter, section) -> text placed inside that section
FACTS = {
    (37, 4): "The Z-axis motor coupling screws (M8, class 12.9) are tightened to 38 Nm in a cross pattern, "
             "then re-checked after 50 operating hours.",
    (13, 2): "The rotary table brake must hold at least 900 Nm; replace the brake disc when the holding "
             "torque falls below this.",
    (30, 3): "Alarm C-2087 (way cover jam): stop the axes, remove chips from the cover lip, and replace "
             "the cover wiper strip, part HX9-4471, if it is torn.",
    (5, 5): "The spindle cartridge on the HX-900 is rated for 20,000 rpm and must be returned to Helix for "
            "rebuild every 15,000 operating hours.",
}
LONG = (22, 6)  # the very long section; its fact goes near the end
LONG_FACT = ("Commissioning sign-off: the machine may be released to production only after the 72-hour "
             "dry run is completed without any alarm and the commissioning engineer and the plant "
             "manager have both signed form HX9-COM-7.")


def manual() -> str:
    out = ["# HX-900 Horizontal Machining Center - Service Manual\n",
           "This service manual covers the HX-900 horizontal machining center. It is for Level 1 and "
           "Level 2 technicians.\n"]
    for c in range(1, 41):
        topic = TOPICS[(c - 1) % len(TOPICS)]
        out.append(f"## {c}. {topic.title()} {'maintenance' if c % 2 else 'service'}\n")
        out.append(paragraph(topic, 3) + "\n")
        for s in range(1, 7):
            out.append(f"### {c}.{s} {R.choice(VERBS).title()} the {topic} {R.choice(PARTS)}\n")
            if (c, s) == LONG:
                body = [paragraph(topic, 8) for _ in range(40)]
                body.insert(37, LONG_FACT)
                out.append("\n\n".join(body) + "\n")
                continue
            text = paragraph(topic, R.randint(5, 9))
            if (c, s) in FACTS:
                half = len(text) // 2
                cut = text.find(". ", half) + 2
                text = text[:cut] + FACTS[(c, s)] + " " + text[cut:]
            out.append(text + "\n")
    return "\n".join(out)


def catalogue() -> str:
    rows = []
    for i in range(500):
        number = f"HX9-{4000 + i * 3 + R.randint(0, 2)}"
        rows.append([number, f"{R.choice(TOPICS)} {R.choice(PARTS)}", R.randint(150, 48_000),
                     R.randint(0, 120), R.randint(2, 70), R.choice(SUPPLIERS)])
    # fixed rows the questions ask about
    rows[157] = ["HX9-4471", "way cover wiper strip", 2_350, 14, 10, "Parker"]
    rows[322] = ["HX9-4970", "spindle cartridge rebuild kit", 412_000, 1, 95, "Schaeffler"]
    for r in rows:
        if r[0] != "HX9-4970" and r[4] >= 95:
            r[4] = 94  # the rebuild kit has the longest lead time
    head = ("# HX-900 Spare Parts Catalogue\n\n## Parts list\n\n"
            "| Part number | Description | Price (INR) | In stock | Lead time (days) | Supplier |\n"
            "|---|---|---|---|---|---|\n")
    return head + "\n".join(f"| {a} | {b} | {c:,} | {d} | {e} | {f} |" for a, b, c, d, e, f in rows) + "\n"


def spindle_log() -> str:
    lines = ["# HX-900 Spindle Log (export)\n\n## Sheet: Spindle log\n",
             "| Timestamp | Spindle speed (rpm) | Spindle load (%) | Bearing temperature (C) |",
             "|---|---|---|---|"]
    for i in range(10):
        lines.append(f"| 2026-09-01T{i:02d}:00:00Z | {R.randint(6, 18) * 1000:,} | {R.randint(20, 70)} | "
                     f"{R.randint(28, 46)} |")
    lines.append("\n*Showing first 10 of 2,000 rows. The file covers hourly readings from 2026-09-01 to "
                 "2026-11-23; mean bearing temperature 37.8 C; the highest bearing temperature was 61.4 C on "
                 "2026-10-07T14:00Z, when alarm A302 was raised.*\n")
    return "\n".join(lines)


def main() -> None:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    (out / "hx900_service_manual.md").write_text(manual(), encoding="utf-8")
    (out / "hx900_spare_parts.md").write_text(catalogue(), encoding="utf-8")
    (out / "hx900_spindle_log.md").write_text(spindle_log(), encoding="utf-8")
    for f in sorted(out.glob("*.md")):
        print(f.name, f.stat().st_size)


if __name__ == "__main__":
    main()
