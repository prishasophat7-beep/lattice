"""Whole conversations on several web-platform exports, as a user has them: a question, the question
back, a picked option, follow-ups, Next / Back, "Same step in", the closest sections.

Run on the demo model (no language model): what is checked is what the app does without one, which
section is read and where the conversation is after each turn, on manuals of different kinds, so a
fix made for one manual cannot quietly break another. Each turn is one of
    {"ask": question}                    typed in the question box
    {"pick": label}                      an option of the question back
    {"same_step": label}                 a "Same step in:" button under the last answer
    {"closest": title}                   a "The closest I found:" button
    {"next": True} / {"back": True}      the Next / Back buttons of the "Working in" line
with what must follow:
    asks=[labels]   a question back with exactly these options     no_ask=True   none
    reads=text      the first source's place contains this         at=title      "Working in" ends here
    starts=text     the answer starts with this                    same_step=[labels]  the buttons
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from oasis_engine.api import create_app
from oasis_engine.demo import demo_model
from oasis_engine.llm import LLMConfig
from oasis_engine.settings import Settings

APP = Path(__file__).resolve().parents[2]
KBS = {
    # a software guide in modules, each with the same steps, some done in two ways (numbered headings)
    "inspectai": APP / "sample-data" / "conversations" / "inspectai_studio_kb.zip",
    # a 282-section service manual: 41 maintenance chapters of numbered steps, two chapters repeated
    "helix_large": next((p for p in (APP / "sample-data" / "helix" / "Helix_Large_knowledge_base.zip",
                                     APP.parent / "Helix_Large_knowledge_base.zip") if p.exists()),
                        APP / "sample-data" / "helix" / "Helix_Large_knowledge_base.zip"),
    # flat documents: a handbook, an IT guide, a training module; product guides; support documents
    "acme": APP.parent / "sample_kb_acme_people_it.zip",
    "nimbus": APP.parent / "sample_kb_nimbus_x2_product.zip",
    "skylink": APP.parent / "sample_kb_skylink_support.zip",
}

CONVERSATIONS = {
    "inspectai": [
        [  # a step every module has: which module, then which way, then on through the module
            {"ask": "How do I import images?", "asks": ["Image Classification", "Anomaly Detection"]},
            {"pick": "Anomaly Detection", "asks": ["Option A: From a folder", "Option B: From a camera"],
             "at": "3.2 Importing Images"},
            {"pick": "Option B: From a camera", "reads": "Option B: From a camera", "at": "3.2.2 Option B: From a camera"},
            {"ask": "How do I train it?", "reads": "Anomaly Detection > 3.3 Training", "at": "3.3 Training",
             "same_step": ["Image Classification", "Text Reading (OCR)"]},
            {"next": True, "reads": "Anomaly Detection > 3.4 Validation", "at": "3.4 Validation"},
            {"same_step": "Image Classification", "reads": "Image Classification > 2.5 Validation", "at": "2.5 Validation"},
            {"back": True, "reads": "Image Classification > 2.4 Training"},
        ],
        [  # a whole module, one step at a time; a step done in two ways is asked back when asked about
            {"ask": "Walk me through text reading", "starts": "**Step 1 of 4: Project Setup**",
             "reads": "Text Reading (OCR) > 4.1 Project Setup"},
            {"next": True, "at": "4.2 Annotating Text"},
            {"ask": "How do I annotate the text?", "asks": ["Method 1: Manual annotation", "Method 2: Assisted annotation"]},
            {"ask": "How do I annotate with method 2?", "reads": "Method 2: Assisted annotation", "no_ask": True},
        ],
        [  # questions that name one place: answered there, nothing asked back
            {"ask": "What learning rate should I use?", "no_ask": True, "reads": "5.2 Learning Rate"},
            {"ask": "How do I activate the licence on an offline workstation?", "no_ask": True,
             "reads": "1.3 Activating the Licence"},
            {"ask": "How many epochs for image classification training?", "no_ask": True,
             "reads": "Image Classification > 2.4 Training"},
        ],
    ],
    "helix_large": [
        [  # numbered maintenance steps: walked through, and a follow-up stays in its chapter
            {"ask": "Walk me through spindle cartridge service", "reads": "2.1 Measure the spindle cartridge filter",
             "starts": "**Step 1 of 6: Measure the spindle cartridge filter**"},
            {"next": True, "reads": "2.2 Tighten the spindle cartridge seal"},
            {"ask": "How do I check the belt?", "reads": "2.6 Check the spindle cartridge belt"},
            {"back": True, "reads": "2.5 Inspect the spindle cartridge seal"},
        ],
    ],
    "acme": [
        [  # a flat handbook: no question back, Next follows the handbook
            {"ask": "How many days of paid time off do new employees get?", "no_ask": True,
             "reads": "Working Hours and Leave", "same_step": []},
            {"next": True, "reads": "Compensation and Benefits"},
        ],
    ],
    "nimbus": [
        [
            {"ask": "How do I mount the X2 on a wall?", "no_ask": True, "same_step": []},
            # (which document it reads is the demo model's pick: it reads the user guide's preface)
            {"ask": "What changed in firmware 3.2?", "no_ask": True},
        ],
    ],
    "skylink": [
        [  # a new topic leaves the place the conversation was in
            {"ask": "My R7 router keeps dropping the Wi-Fi connection. What should I check?", "no_ask": True,
             "reads": "r7_router_troubleshooting_guide"},
            {"ask": "How long is the warranty on the router?", "no_ask": True, "reads": "skylink_limited_warranty"},
        ],
    ],
}


def client_for(kb: Path, tmp_path: Path) -> TestClient:
    client = TestClient(create_app(Settings(data_dir=tmp_path / "data", llm=LLMConfig(model="demo")),
                                   model=demo_model()))
    r = client.post("/api/kb/import", json={"path": str(kb)})
    assert r.status_code == 200, r.text
    return client


def place_of(result: dict) -> str:
    """The first source as "<file> > <place>" (what the reads= checks look in)."""
    c = (result.get("citations") or [{}])[0]
    return f"{c.get('doc_name', '')} > {c.get('locator', '')}"


def play(client: TestClient, conversation: list[dict], cid: str) -> list[str]:
    """Play a conversation; the problems found, one line each (empty: as expected)."""
    problems, last = [], None
    for n, turn in enumerate(conversation, 1):
        body = {"conversation_id": cid}
        if "ask" in turn:
            body["question"] = turn["ask"]
        elif "pick" in turn:  # an option of the question back, as the app sends it
            option = next((o for o in (last.get("clarify") or {}).get("options", []) if o["label"] == turn["pick"]), None)
            if option is None:
                return problems + [f"turn {n}: no option {turn['pick']!r} to pick"]
            body.update(question=option.get("question") or last["question"], doc_ids=option["doc_ids"],
                        section=option.get("section"), replaces=last["history_id"])
        elif isinstance(turn.get("same_step"), str):
            focus = last.get("focus") or {}
            button = next((e for e in focus.get("elsewhere", []) if e["label"] == turn["same_step"]), None)
            if button is None:
                return problems + [f"turn {n}: no 'Same step in' button {turn['same_step']!r}"]
            body.update(question=f"{button['title']} in {button['label']}", doc_ids=[focus["doc_id"]],
                        section=button["node_id"])
        elif "closest" in turn:
            c = next((c for c in last.get("considered", []) if c["title"] == turn["closest"]), None)
            if c is None:
                return problems + [f"turn {n}: no closest section {turn['closest']!r}"]
            body.update(question=f'What does "{c["title"]}" say?', doc_ids=[c["doc_id"]], section=c["node_id"])
        elif turn.get("next") or turn.get("back"):
            focus = last.get("focus") or {}
            title = focus.get("next" if turn.get("next") else "previous")
            if not title:
                return problems + [f"turn {n}: no {'Next' if turn.get('next') else 'Back'} button"]
            body["question"] = f"Next: {title}" if turn.get("next") else f"Go back: {title}"
        if last and last.get("focus") and "focus" not in body:
            body["focus"] = {"doc_id": last["focus"]["doc_id"], "node_id": last["focus"]["node_id"]}
        r = client.post("/api/ask", json=body)
        if r.status_code != 200:
            return problems + [f"turn {n}: HTTP {r.status_code} {r.text[:200]}"]
        result = r.json()
        said = body["question"]
        asked = [o["label"] for o in (result.get("clarify") or {}).get("options", [])]
        if "asks" in turn and asked != turn["asks"]:
            problems.append(f"turn {n} ({said!r}): asked back {asked}, expected {turn['asks']}")
        if turn.get("no_ask") and asked:
            problems.append(f"turn {n} ({said!r}): asked back {asked}, expected an answer")
        if "reads" in turn and turn["reads"] not in place_of(result):
            problems.append(f"turn {n} ({said!r}): read {place_of(result)!r}, expected {turn['reads']!r}")
        at = ((result.get("focus") or {}).get("path") or [""])[-1]
        if "at" in turn and at != turn["at"]:
            problems.append(f"turn {n} ({said!r}): working in {at!r}, expected {turn['at']!r}")
        if "starts" in turn and not result["answer"].startswith(turn["starts"]):
            problems.append(f"turn {n} ({said!r}): answer starts {result['answer'][:60]!r}")
        if isinstance(turn.get("same_step"), list):
            labels = [e["label"] for e in (result.get("focus") or {}).get("elsewhere", [])]
            if labels != turn["same_step"]:
                problems.append(f"turn {n} ({said!r}): 'Same step in' {labels}, expected {turn['same_step']}")
        last = result
    return problems


@pytest.mark.parametrize("kb", sorted(CONVERSATIONS))
def test_conversations(kb, tmp_path):
    if not KBS[kb].exists():
        pytest.skip(f"{KBS[kb].name} is not in this checkout")
    client = client_for(KBS[kb], tmp_path)
    problems = [f"conversation {i}: {p}" for i, conv in enumerate(CONVERSATIONS[kb], 1)
                for p in play(client, conv, f"{kb}-{i}")]
    assert not problems, "\n".join(problems)
