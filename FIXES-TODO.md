# Lattice 0.1.0 (20 Oct 2026 build): fixes to apply

Found while testing on 22 Oct 2026 with the sample knowledge bases, on the built-in Qwen3.5-4B
model and on DeepSeek Chat through OpenRouter. Paths are relative to `lattice-source/app/`.

Status key: **Confirmed** = reproduced with Lattice's own code on the sample bundles.
**Likely** = mechanism found in the code, not yet reproduced end to end.

Suggested order: 1, 3 (one-line changes), then 2, 4, 7 (largest effect on answers), then 5, 6, 8.

---

## 1. Picture descriptions are cut to their first sentence (Confirmed)

**Symptom:** "How many tasks are overdue on the dashboard?" (TaskFlow) answers "not specified",
although the picture description says "Overdue 3". Affects all four TaskFlow pictures.

**Where:** `backend/oasis_engine/loaders.py`, `BRIEF_DESCRIPTION_CHARS = 400` (line 331) and
`_brief_description()` (lines 341-357).

**Why:** descriptions longer than 400 characters are reduced to the "GLOBAL SCENE" part plus words
in *double* quotes. The TaskFlow descriptions are 447-579 characters and quote with *single* quotes,
so the model only gets the first sentence. The full text is kept in `image_notes` but is only shown
to the user, never given to the model.

**Change:**
```python
BRIEF_DESCRIPTION_CHARS = 1000   # was 400
```
Optionally also accept single-quoted labels in the `re.findall(...)` inside `_brief_description`
(note: adding a second capture group makes `findall` return tuples; flatten them).

**Tested:** with 1000, the model's text for the dashboard picture includes "Overdue 3" and
"Welcome back, Priya".

**Re-test:** TaskFlow: overdue count (3), name on dashboard (Priya), Create Project fields,
burndown chart, leave workflow diagram.

---

## 2. Server models get only ~200-character previews of sections (Confirmed)

**Symptom (DeepSeek Chat via OpenRouter, Helix Large):**
- Q2 "Did Arvind Metals meet its on-time delivery target in Q2 2026?": read 2 of 7 rows, assumed
  5 orders, answered 40 % (correct: 4/7 = 57.1 %).
- Q3 "How much late-delivery penalty does Arvind Metals owe for Q2?": invented PO-26-0521 and
  PO-26-0558, answered ₹24,750 (correct: ₹49,050).
- Q1 warranty: missed "Spindle bearings are covered for 12 months only if the warm-up rules in
  5.2 have been followed".

**Where:** `backend/oasis_engine/chat.py`, around line 1205:
```python
passage_for = PASSAGE_HITS if deps.tier == "local" else 0
```
and the `table_highlights` block just below (`if deps.tier == "local":`).

**Why:** the built-in model is given the full text of the top 2 sections and table figures worked
out in code. A server model gets only the search snippet and is expected to call `read_section`;
DeepSeek did not, and filled the gaps by guessing. The same questions on the built-in 4B model
(which gets the full text) answered Q2 correctly.

**Change:**
```python
passage_for = PASSAGE_HITS          # full text of the best matches for every model
```
and consider running `table_highlights` for remote models too.

**Re-test:** Helix Q1-Q3 with an OpenRouter model.

---

## 3. Built-in model's Chat answers are cut off (Likely)

**Symptom:** Helix Q3 on Qwen3.5-4B worked through the orders correctly (PO-0503 = ₹27,600,
PO-0561 = ₹11,700), then stopped with "(The answer was cut off.)" and gave no total.

**Where:**
- `backend/oasis_engine/settings.py` line 48: local `max_tokens` default `2048`.
- `backend/oasis_engine/api.py` line 210: `ChatAgent(chat_model, max_tokens=self.chat_llm.max_tokens, ...)`.
- `backend/oasis_engine/config.py` lines 83-85: remote connections get `max(cfg.max_tokens, 4000)`
  and `thinking_off = True`; the built-in model gets neither.
- `chat.py` line 1138 adds the "(The answer was cut off.)" note when `finish_reason == "length"`.

**Change:** give the built-in model's Chat mode at least 4000 tokens, e.g. in `api.py`:
```python
self.chat = ChatAgent(chat_model, max_tokens=max(self.chat_llm.max_tokens, 4000), ...)
```
Also check whether the built-in Qwen 3.5 is thinking before answering (that would use up the
limit and add minutes); if so, switch thinking off for it as for remote models.

**Re-test:** Helix Q3 on the built-in model; expect the full total ₹49,050.

---

## 4. Second half of a two-part question is searched without its subject (Confirmed)

**Symptom:**
- Skylink Q5 "What caused the ticket spike in Q3 and how should agents fix it?": cause correct,
  but said the documents don't describe the fix (they do: troubleshooting guide 4.3).
- Helix Q1 part 2 "what should operators have done?": generic maintenance tasks instead of
  "run O9000 at the start of every shift".

**Where:** `backend/oasis_engine/plan.py` `question_parts()`, used in `chat.py` around line 1190
(the `PART_HITS` search per part).

**Why:** the parts are searched separately, and the second loses the subject:

| Part searched | Top result |
|---|---|
| "how should agents fix it?" | q3_2026_support_review > Customer Feedback (wrong) |
| "what should operators have done?" | HX-500 > 7.1 Maintenance schedule (wrong) |
| "how should agents fix E-217 firmware update failure?" | r7 guide > 4.3 Firmware Update Failure (right) |

**Change:** when searching parts 2+, add the topic words of part 1 (e.g. append part 1 to the
query, or replace "it/they/this" with part 1's subject).

**Re-test:** Skylink Q5, Helix Q1.

---

## 5. Stray `$` signs in answers (Confirmed)

**Symptom:** "Weeks late: $14 / 7 = 2$ full weeks", "$9,20,000 × 1.5% × 2 = 27,600$".

**Where:** `backend/oasis_engine/chat.py` `plain_math()` (lines 364-380).

**Why:** handles `\[...\]`, `\(...\)` and `$$...$$` but not single `$...$`, and returns early when
the answer has no backslash.

**Change:** strip single `$...$` pairs whose content looks like maths (contains `=`, `×`, `/`, `\`),
before the early return. Leave real amounts like "$5" alone.

**Re-test:** Helix Q3; no `$` in the numbers.

---

## 6. "Numbers match the document" badge on guessed figures (Confirmed)

**Symptom:** DeepSeek's Q2 answer ("assume at least 5 orders ... 40 %") showed the green badge.

**Where:** `backend/oasis_engine/numbers.py` `unverified_numbers()` lines 151-160 and
`_one_step()` lines 195-205; badge in `desktop/src/components/AnswerCard.tsx` line 275.

**Why:** a figure passes if it is one step of arithmetic on other figures of the answer; 40 = 2/5×100
and 2 and 5 occur somewhere in the documents.

**Change:** only allow arithmetic on figures that were themselves found in the sources, or show a
softer badge ("worked out from the document's figures") for derived ones.

---

## 7. Answers from the documents labelled "General answer, not from the company documents" (Likely)

**Symptom (built-in 4B, Office profile):** "How do I annotate text?" (InspectAI) and Nimbus
"What does error E2 mean, why does it happen, and was it fixed?" were both correct and taken from
the manuals, but shown with the general-knowledge warning and no citations.

**Where:** `backend/oasis_engine/chat.py`:
- line 1057-1058: fallback `implied_sources()` when the model wrote no [S1] markers;
- line 1139: `general = not documents_only and (general_part or not (citations or deps.files_used or about_self))`.

**What was checked:** the fallback's word-overlap test would pass (Nimbus answer: 34 % / 30 % of its
words in Troubleshooting / Bug Fixes; annotate answer: 70 %+ in 4.2 Annotating Text) and all figures
are in the documents. So the sections were probably not in `deps.read` / `deps.shown` when the check
ran. Needs a logged run to confirm.

**Change:**
1. If the model read any document sections and wrote no general-knowledge part, show
   `UNSOURCED_NOTE` ("did not name its sources ... check it in the documents") instead of marking
   the answer general.
2. Reproduce the Nimbus E2 question with logging of `deps.read`, `deps.shown` and `citations`.

**Re-test:** InspectAI "How do I annotate text?", Nimbus Q7.

---

## 8. "How do I annotate text?" doesn't ask Method 1 / Method 2 on a first question (Confirmed)

**Where:** `backend/oasis_engine/api.py` `which_way_for()` (lines 1363-1371) and its call around
line 1521.

**Why:** the method question back needs a current section (a picked option or the conversation's
place). A first question has neither, so it returns `None`. `vague_clarification()` also returns
`None` for this question (only the OCR part has annotation).

**Change:** when there is no target, take the best search hit; if `focus.variants(doc, nid)` lists
alternatives, ask back with them.

**Re-test:** InspectAI "How do I annotate text?" -> Method 1 / Method 2 buttons.

---

## 9. Built-in model is slow (3½ to 9½ minutes per answer)

Not one bug. Up to 6 model calls per question (`MAX_REQUESTS = {"local": 6}`, chat.py line 63),
each 1-2 minutes on a laptop CPU. Options (untested): fewer calls for the local tier, shorter
`PASSAGE_CHARS` (900), thinking off (see 3). The README itself quotes 4-11 minutes on 8 GB laptops.

---

## 11. Tests fail with pydantic-ai 2.60 (Confirmed)

`pyproject.toml` asks for `pydantic-ai-slim[openai]>=2.40`. A fresh install picks up 2.60, where
`tests/test_api_manuals.py::test_conversation_context_and_history` fails (a validation retry changes
the prompts the test inspects). 386/386 pass with 2.40, 2.45 and 2.50. Pin an upper bound
(`>=2.40,<2.60`) until the test or the code is adjusted.

## 10. Data issues (not code)

- **Two knowledge bases named "Sample Knowledge Base":** `page_index.json` of
  `sample_kb_taskflow_images.zip` and `sample_knowledge_base.zip` share `kb_name`; Lattice names
  versions from it (`bundle.py` `_export_version`). Rename in the bundle, or fall back to the zip's
  file name when names clash.
- **Missing section summaries** ("1 missing", "12 missing" in Manage): absent in the exported bundles.

---

## Test results, 22 Oct 2026

| Test | DeepSeek Chat (OpenRouter) | Built-in Qwen3.5-4B |
|---|---|---|
| Helix Q1 warranty | wrong reason | reason right, part 2 vague (issue 4) |
| Helix Q2 on-time | 40 % (wrong) | 57.1 % (right) |
| Helix Q3 penalty | ₹24,750, invented orders | right so far, cut off (issue 3) |
| TaskFlow overdue | - | "not specified" (issue 1) |
| InspectAI "How do I train?" | - | asked back instantly (OK) |
| InspectAI "How do I import images?" | - | asked back, 2 correct options (OK) |
| InspectAI "How do I annotate text?" | - | no ask-back (8), labelled general (7) |
| Skylink Q5 spike + fix | - | cause right, fix missed (issue 4) |
| Skylink Q6 warranty + firmware | - | right, both documents cited |
| Nimbus Q7 E2 | - | right, labelled general (issue 7) |
| Acme Q8 phishing | - | right, cited |

Expected answers: Q2 4/7 = 57.1 %; Q3 PO-26-0503 ₹27,600 + PO-26-0561 ₹11,700 + PO-26-0590 ₹9,750
= ₹49,050 (Amendment 1: 1.5 % per full week); Q1 bearings covered 12 months only if warm-up (O9000 at
every shift start, after stops > 4 h) was followed.
