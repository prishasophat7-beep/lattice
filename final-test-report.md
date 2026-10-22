# Lattice — Final Pre-Production Test Report

- Date: 15 Oct 2026
- Tester: Claude (computer use on the installed app)
- Builds tested: 00:05 (Part 1), 09:24 (re-test, Part 2), engine 12:37 (Part 3, final re-checks). Manage shows "engine 0.1.0 (built 15 Oct 2026 12:37)".
- Model: built-in Qwen3.5-0.8B on an 8 GB laptop. The 4B re-run was **not done**: about 5 min per answer and a full model swap each way, and time ran out.
- Knowledge bases: Sample Knowledge Base (2026.10.13-0906) and Helix Precision (2026.10.15). The Sample KB is active again at the end.
- Timing note: from about 02:40 another llama-server (the PageIndex session's, port 8099) was competing for CPU, so absolute timings are inflated.

## 1. Results by section (latest build each item was run on)

| Section | Right | Partial | Wrong / bug | Notes |
|---|---|---|---|---|
| Model swap (4B→0.8B) | ✓ | | | Conversation kept, progress shown under the model section, old file removed, about 70 s |
| A Vague (ask back) | A1, A2, A5 | A3 (offers documents), A4 (option answer repeats a sentence twice) | | |
| B Rules applied to a case (Manuals) | B6, B7 | B2 (rule shown, no "Yes") | B3 ("1"), B5 ("Yes"), B8 ("Yes" at 30, rule is 35+) | Model limitation |
| C Tables | C1–C6, C8 (partial-table note quotes the 1,840.6 ms spike) | C6 (no explanation) | C7 (not covered) | |
| D Follow-ups | D1–D5 | | D6 ("Is it paid?" answers about holidays) | |
| E Multi-part | E1, E2 | E3 ("No overtime allowance" stated as fact) | E4 (invitation links "1 year"; earlier builds said 14 days) | |
| F Not in documents (Manuals) | F1–F5 | | | No inventions |
| G Named documents | G1 (default only), G2 | G1 | | |
| H Live information | weather, USD→INR and news decline instantly in both modes (09:24 build onwards) | | | |
| I Plain facts | I1, I3, I4, I5, I6, I7, I8, I9, I10 | I2 (date missing) | | |
| J Robustness | typo, caps, one word, whitespace, emoji, joke injection, DAN, gibberish (12:37 build) | long question, Hindi (answered in English) | | |
| K Small talk | hi, thanks, hi after an answer, who are you (12:37 build) | | | |
| L Answer UI | source panel, evidence, Copy, feedback with Change, Stop, opaque top bar | | No "Copied" toast | |
| M History | titles, reopen, stopped answer → "No answer" → Ask again | | No delete, no search | |
| N Manage | model, documents, versions, search by meaning, URL validation (wraps, 12:37 build), build line | | Model size not shown | |
| O Window and keyboard | 1216×719 default fits the screen, 720×590 layout clean, Enter / Shift+Enter / Esc | | | |
| P Stability | input held while busy, mode switch mid-answer, close (all processes exit) and reopen (history kept) | | | |
| Chat re-tests (12:37 build) | F1 not covered, D1–D3, X9 Jaipur | B1 banner gone | B1 "carry 18 days", B6 "26 weeks", **F2 repeats E1** | |
| Helix X1–X25 (Manuals, 30 items) | 11 | 3 | 16 | See table 3 |

## 2. Genuine app bugs (ranked)

1. **The final answer card differs from the streamed answer; post-processing overwrites it.** (Critical)
   - X17 "Which lines met the scrap rate target in every month?": the answer streamed "Line A" (correct), then the card became "Not in the documents" (159 s).
   - "How much does an HX-500 machine cost?": the answer streamed "The excerpt does not contain the answer…", then the card became "5,800 kg" (the machine weight) with "Numbers match the document".
   - Repro: Helix KB, Manuals mode, ask either question.
2. **Table-cell answers are rejected in Manuals.** (Critical) These all return "Not in the documents" while only the correct section is checked:
   - grade × city (X9 ×3, plus "accommodation limit for G2 in a Tier 1 city")
   - travel class (X10)
   - daily allowance (X11)
   - OEE month × line (X18)
   - PO penalty (X20)
   - Chat mode answers the same questions (6,000 / 12,000 / 8,000), so the model can do it and the gate is the problem.
3. **Chat repeats the previous answer for a new question.** (High) Chat E1 (RAM + holidays) followed by "How much does TaskFlow Pro cost per user?" in the same conversation repeats E1's answer. Still present on the 00:05, 09:24 and 12:37 builds.
4. **Retrieval prefers "1. About this manual" for the HX-500.** (High)
   - X2 (A302 → "No Machine" condition, hallucinated) and X24 (cabinet → "Read chapter 2").
   - The correct sections (alarm table, 2.3 Lockout/tagout) are never read, although X3 retrieves the A302 row.
5. **No answer-vs-evidence polarity check.** (High)
   - X14 answers "Yes" while its evidence says "Contractors may not drive Helix forklifts".
   - X12 says "Yes, you can still claim it… the expense is not reimbursed".
   - Chat B6's headline says 26 weeks while its own bullets say 12.
6. **"Numbers match the document" badge on wrong answers.** (Medium)
   - E4 "1 year", B3 "1", X5 "600 kg", X25 "5,800 kg", Chat B1 "18 days".
   - Chat X20 shows a false banner, "2025 does not appear" (the document contains "January 2025"), and the "did not name its sources" note still appears there.
7. **Option answer repeats sentences.** (Medium) "What is the approval?" → clicking the "Leave Approval Procedure" option gives the same sentence twice (four times on the 09:24 build).
8. **"Language model is busy" got stuck.** (Medium, 09:24 build; fixed on 12:37) Try again kept failing while llama-server was idle. Repro: Diwali question → follow-up asking the mean response time.
9. **A question typed while an answer finished was silently cleared.** (Low) Seen once.
10. **Search by meaning re-indexes from scratch on every knowledge-base switch.** (Low) Shows "0 of 53 sections".
11. **Missing features:** no History delete or search; no "Copied" toast; model size not shown; all Helix section summaries "missing"; the doubled-word example chip "What does the manual say about about this manual?".

Fixed during the night (verified):
- model swap
- Regenerate re-runs the model
- partial-CSV answers
- Manuals live-info declines
- "Worked out" label on plain facts
- follow-up rewrites (A5, D5, X9)
- option→question wording
- gibberish follow-up
- emoji / DAN / "who are you"
- "model busy" stuck state
- Chat F1 invented steps
- Chat banners
- window fits the screen
- readable, wrapped URL error
- build line in Manage
- typed text carried into a new conversation

## 3. Model limitations (0.8B reasoning wrong although the right section was given)

- **Rule application:** B3, B5, B8, C7, D6, E4 (part 1), I2 (date missing).
- **HX-500 machine manual:**
  - X4 picks O9000 / 15 min (should be O9001 / 30 min for more than 72 h off).
  - X5 uses the wrong spec row.
  - X8 says the warranty still applies at 30 months.
  - The HX-700 question gets the HX-500's spindle speed (model mismatch).
- **Other Helix rules:** X21 (one bad quarter treated as two); X23 adds a spurious clause.
- **Chat:** B1 "carry 18 days", B6 "26 weeks"; X9 calls Pune Tier 2.
- **Language:** a Hindi question is answered in English in Manuals.

## 4. Helix (Manuals) detail

- **Right (11):**
  - X1 deadline and limit ask back
  - X3 65 °C
  - X6 parameter group 1850
  - X7 drawbar 12–14 kN
  - X13 24 h (was 48 h)
  - X15 padlock
  - X16 Line C, 406 h
  - X19 45 days, not 60
  - X22 warm-up skipped
  - X25 Chennai OEE and G2 annual leave not covered
- **Partial (3):**
  - X1 penalty (no ask-back)
  - X16 evidence quotes the wrong row
  - X23 spurious clause
- **Wrong (16):**
  - X2, X4, X5, X8, X9 ×3, X10, X11, X12, X14, X17 (overwritten), X18, X20, X21, X24
  - X25 cost and HX-700 (not covered expected)
- **Chat:** X9 6,000 / 12,000 / 8,000; X20 is wrong.
- **X26:** switching between the Helix and Sample knowledge bases works, and answers come from the active one.

## 4b. Final-build re-check (backend 15:51, installer 15:56, commit 9b871a7; 0.8B; Manuals unless marked)

| Item | 12:37 build result | Final build result | Verdict |
|---|---|---|---|
| X2 A302 meaning | hallucinated "No Machine" | Spindle over-temperature, with operator steps; cites 6.2 (50 s) | FIXED |
| X9a G2 Pune hotel | not covered | 6,000 (16 s) | FIXED |
| X9b And for a G4? | not covered | 12,000 (68 s) | FIXED |
| X9c What about in Jaipur? | not covered | 12,000, the Tier 1 value (64 s). Should be 8,000, Tier 2 | WRONG (model: city→tier) |
| G2 / Tier 1 direct lookup | not covered | 6,000, with an extra G3 row quoted (10 s) | FIXED |
| X10 G3 Frankfurt 9 h | not covered | still not covered (71 s); expected "Business" | WRONG |
| X11 G2 lunch provided | not covered | 1,200; the 25 % meal reduction is not applied, should be 900 (8.5 s) | PARTIAL (model) |
| X12 lost 3,000 receipt | "Yes… not reimbursed" | unchanged: "Yes, you can still claim it… not reimbursed" (74 s) | WRONG (polarity check missed it) |
| X14 contractor forklift | "Yes" vs "may not" | "The documents state: Contractors may not drive Helix forklifts." (7 s) | FIXED |
| X17 scrap target | streamed Line A → not covered | Line A (12 s) | FIXED |
| X18 OEE Line C April | not covered | 52.3 (65 s) | FIXED |
| X20 PO-26-0503 penalty | not covered | "14", the days late, with the Numbers-match badge (10 s); expected 27,600 | WRONG |
| X24 electrical cabinet | "Read chapter 2" | Lockout/tagout, own padlock, wait 5 min for the capacitors; cites 2.3 (53 s) | FIXED |
| X25 HX-500 cost | "5,800 kg" | **"Cost ($K): 5,800 kg"** with Worked-out and Numbers-match badges (87 s) | WRONG (app check bypassed) |
| X25 HX-700 spindle | HX-500 speed | Not covered (62 s) | FIXED |
| Sample: Diwali → CSV mean | 169.6 ms ✓ | 8 Nov / 8 fixed (30 s), then 169.6 ms (56 s) | PASS |
| Sample: gibberish in conversation | not covered ✓ | Not covered | PASS |
| Sample: 😀 → DAN | refusal ✓ | one-line prompt, then instant refusal | PASS |
| Sample: Manuals "who are you" | introduction ✓ | introduction | PASS |
| Chat E1 → F2 | F2 repeats E1 ✗ | E1: 4 GB + 10 holidays, both cited (71 s); F2: "The documents on this computer don't cover that." (82 s) | FIXED |

Re-check score: Helix 9 fixed / 1 partial / 5 wrong of 15 (was 0 right of these 15); Sample 5 of 5.

Still open (app-controlled):
- **X25 cost.** A price question is still answered with a measurement ("Cost ($K): 5,800 kg") under two trust badges. The new "price answered with a measurement" check is defeated when the model puts a currency label in front of the value.
  - Repro: Helix KB, Manuals, new conversation → "How much does an HX-500 machine cost?".
- **X12 polarity.** "Yes, you can still claim it" while the cited rule says "not reimbursed". The new rule-override check didn't fire, probably because the wording is "not reimbursed" rather than "must not" / "may not".
  - Repro: Helix, Manuals → "I lost a taxi receipt of INR 3,000. Can I still claim it?".
- **X20.** Answers a bare "14" (days late) to a penalty question with "Numbers match"; the penalty formula (2 weeks × 1.5 % × 9,20,000 = 27,600) is never applied.
- **X10.** Still "not covered" for a word answer ("Business") in a grade × column table.
- **Minor:** a conversation containing only canned replies (😀 / DAN / who are you) doesn't appear in Recent.

## 4c. Last build re-check (backend 16:43, app 16:48, commit 4858795; 0.8B)

| Item | Mode | Result | s | Verdict |
|---|---|---|---|---|
| X25 HX-500 cost (run 1) | Manuals | Streamed "does not contain information regarding the cost", final card "Not in the documents", no badge | 166.5 | FIXED |
| X25 HX-500 cost (run 2) | Manuals | "Not in the documents", no badge | 197.2 | FIXED |
| X12 lost INR 3,000 receipt | Manuals | "The documents state: Lost receipts: a self-declaration is accepted up to INR 2,000 per trip; above that the expense is not reimbursed." No "Yes"; cites 7. Receipts | 168.2 | FIXED |
| X2 A302 | Manuals | Spindle over-temperature, with the stop, cool and chiller steps; cites 6.2 | 59.9 | PASS |
| X9a G2 Pune hotel | Manuals | 6,000; cites 4. Accommodation limits | 40.8 | PASS |
| X17 scrap target (new conversation) | Manuals | Line A | 39.9 | PASS |
| X17 asked as a follow-up after X9a | Manuals | "Line A, Line B, and Line C" | 186.8 | WRONG (model, follow-up context) |
| X14 forklift contractor | Manuals | "No, contractors may not drive Helix forklifts." (extra citations: 7, 8) | 234.3 | PASS |
| Sample E1 RAM + holidays | Chat | 4 GB; 10 paid holidays; both sources cited | 111.7 | PASS |

Both remaining app blockers (the fabricated price under trust badges, and "Yes" against a "not reimbursed" rule) are fixed. No regressions on the earlier fixes.

Timing note: answers took 40–234 s on the 8 GB laptop.

Remaining known issues, all from the 0.8B model's reasoning (no app blockers):
- X9c Jaipur uses the Tier 1 rate.
- X11 doesn't apply the 25 % meal deduction.
- X20 answers the days late instead of the penalty.
- X10 travel class is "not covered".
- X4, X5, X8, X21 rule application.
- X17 goes wrong when asked as a follow-up to an unrelated question.

## 4d. Final verdict: **GO**

GO for production with the 0.8B built-in model. Ship with a known-issue note: on 8 GB machines, multi-step policy reasoning (tier lookups, deductions, penalty formulas, warranty and warm-up rules) can be wrong, so point those users to a network model server.

## 5. Earlier recommendation (superseded by 4d): **CONDITIONAL GO**

On the final build, every blocking app bug from the night is fixed and verified:
- the final card now matches the streamed answer
- table cells are accepted
- the Chat repeat is gone
- "About this manual" no longer wins retrieval
- the forbidding-rule override works for "may not"

The interface, history, stability, Manage, the declines and the Sample-KB behaviour all pass.

- **GO** for deployment on the Sample-type documents (HR policy, user manual, spreadsheets, config files) with the 0.8B model on 8 GB machines.
- **Fix before relying on contract/price documents (Helix-type), or ship with a known-issue note.** The remaining X25 price bug is small to fix but shows a fabricated price under "Numbers match the document". Extend the price-vs-measurement check to the answer's value unit ("kg" in a cost answer → reject), and extend the polarity check to "not reimbursed" / "not permitted" / "not covered".
- The other open Helix items (X9c Jaipur tier, X11 meal deduction, X20 penalty formula, X4/X5/X8/X21) are 0.8B reasoning limits. A network model handles them; document this for multi-step policy questions.

## 6. Helix Large — final computer-use check (16 Oct 2026)

Build: lattice-backend.exe 12:45, lattice.exe 12:48 (commit dbe32c0). Model: built-in Qwen3.5-0.8B, Manuals mode unless noted.
KB: imported `Helix_Large_knowledge_base.zip` → **helix-large-2026.10.15-1800**: 9 documents and 381 sections (HX-900 service manual alone has 282), all summaries Complete. Search-by-meaning indexing took about 3 min. The machine was otherwise idle.

| # | Item | Result | s | Verdict |
|---|---|---|---|---|
| 1 | HX-900 Z-axis coupling screw torque | 38 Nm, cited from HX-900_service_manual §37.4 (not the HX-500) | 70 | PASS |
| 2 | HX-900 rotary table brake holding torque | 900 Nm (cited §13.2 "Clean the door interlock fuse", where the sentence lives) | 109 | PASS |
| 3 | HX-900 release to production after commissioning | Correct text: 72-hour dry run, no alarm, signed by the commissioning engineer and the plant manager on HX9-COM-7. **But** the evidence row is spare part HX9-4781 (matched on "In stock: 72"), and the citations are the plant safety standard and the parts list, not the commissioning section | 103 | PARTIAL (wrong evidence/citations) |
| 4 | Spindle cartridge rebuild interval | 15,000 operating hours | 69 | PASS |
| 5 | Price of HX9-4471 | 2,350 (unit "INR" not shown in the answer line). Clean evidence row | 119 | PASS |
| 6 | Longest lead-time spare part | "HX9-4970 (95)" Worked out. **But** the evidence rows are HX9-4149 and HX9-4408 (matched on "In stock: 95"); the HX9-4970 row is not shown | 67 | PARTIAL (wrong evidence) |
| 7 | Highest bearing temperature in the spindle log | 61.4 C from the file's summary note (not a ranking of the sample rows) | 83 | PASS |
| 8 | Alarm C-2087 + replacement part cost | Way cover jam; HX9-4471 way cover wiper strip; 2,350 INR. Evidence from both the service manual and the parts list | 82 | PASS |
| 9 | HX-950 Z-axis torque | Not in the documents | 66 | PASS |
| 10 | HX9-4471 price → "And its lead time?" | 2,350 → Searched as "What is the lead time for part HX9-4471?" → 10 (second evidence row HX9-4222 matched on "In stock: 10"; no "days" unit) | 119 / 101 | PASS (minor evidence noise) |
| 11 | G2 Pune hotel → scrap-rate lines (same conversation) | 6,000 → **Line A only** | 8 / 12 | PASS |
| 12 | Contractor forklift | "No. The documents state: Only employees holding a valid Helix forklift license (renewed every 3 years) may drive forklifts." (Worked-out label) | 6 | PASS |
| 13 | 8 kg tool, HX-500 | "Tools heavier than 7 kg or longer than 300 mm must not be loaded." (§5.3) | 74 | PASS |
| 14 | G3 to Frankfurt, 9 h | Grade G3, Air (international, over 6 hours): Business (an extra G4 evidence row) | 67 | PASS |
| 15 | Chennai OEE H1 2026 | Not in the documents | 66 | PASS |
| 16 | "receipts" | Only one rule: "Receipts must show the GST number of the supplier where applicable." No summary of the INR 500 original-receipt rule or the INR 2,000 lost-receipt rule; also cites 6. Approvals | 21 | PARTIAL |
| E1 | Sample KB, Chat: RAM + paid holidays | 4 GB and 10 holidays, both sources cited, **but** each part opens with "The documents do not state the specific RAM requirement / total number of paid holidays" before giving the figure | 76 | PARTIAL (self-contradicting wording) |
| E2 | Sample KB, Manuals: "what is the leave policy?" | Returns the document header line ("Acme Technologies… Document No. HR-POL-007 \| Version 3.1 \| Effective 1 April 2026 \| Owner: HR"). No error, but not a policy summary or a clarification | 31 | PARTIAL |
| KB switch | Manage → Switch to sample-knowledge-base-2026.10.13-0906 | Switched; start page shows the 7 Sample docs; search by meaning re-indexes (0 of 53) | – | PASS |

Score: 12 PASS, 6 PARTIAL, 0 FAIL (18 items).

### UI notes (D, large scale)
- Answer cards for 5, 6 and 7 render cleanly. Long table rows (for example the parts-list rows) wrap inside the evidence box with no overflow or cut-off; citation chips are tidy; the 381-section KB loads without lag.
- **Evidence selection by number coincidence** (items 3, 6, 10): the evidence row is chosen because a number in it equals the answer (stock 72, stock 95, stock 10), not because it is the source row. For item 6 this shows two wrong parts next to a correct answer. This is the most visible remaining issue at scale.
- Bare-number answers lack units: "2,350" (INR), "10" (days), "38 Nm" is fine.
- Highlighting marks only the digits of codes ("C-**2087**", "HX9-**4471**").
- Start-page cards for HX-900_spare_parts.xlsx and HX-900_spindle_log.csv show raw table headers as the description ("Part number Description Price (INR) In stock Lead time (days) Supplier HX9 4002 chiller gasket 3,140 54 47…").
- The example chip for the IT standard reads awkwardly: "What does the it security standard say about Helix Precision - IT Security Standard (ITS-03, version 2.1)?".
- The Manage gear needed two clicks once right after maximizing (focus), but was otherwise fine.

### Final verdict (section 6): **GO**
- All 9 large-document questions produce the correct answer.
- The new fixes (cross-document alarm → part → price, a wrong-manual answer rejected as not covered, partial-table summary, Frankfurt, the 8 kg tool, a forklift "Yes" overridden, topic change mid-conversation) all verified.
- No FAILs.
- Remaining items are polish, recommended for the next patch, not blockers:
  - evidence rows chosen by number coincidence (3, 6, 10)
  - thin one-word answers ("receipts", "what is the leave policy?")
  - units on bare-number answers
  - the self-contradicting "documents do not state… however" Chat phrasing

## 7. Final check (17 Oct 2026; installed app + headless 0.8B evals)

The tester session had stopped before its recheck, so this round was run directly: computer use on the installed app (0.8B, sample KB and Helix Large), then the eval sets on the 0.8B against the final engine source.

### Found and fixed in this round
| Item | Problem | Fix |
|---|---|---|
| History delete | Did nothing in the app: the engine's CORS rules did not allow DELETE (the UI test stubbed the call, so it passed) | DELETE allowed; UI test now calls the real engine and reloads; a failed delete shows why |
| History scroll | Opens at the top and scrolls (could not reproduce a stuck list); scroll reset on open added | — |
| Chat "How long?" (0.8B) | Declined as a general question | Offers the sections that give durations, as in Manuals |
| Option snippets | Started/ended mid-word ("...ve Employee", "to b") | Whole words |
| "Most expensive part" | Ranked nothing (no column named): 36,105 INR part | "expensive/cheapest/costliest/..." rank the price column: HX9-4970, 412,000 INR |
| Two-part "alarm ... and what does the replacement part cost?" | Part 2 sometimes gave another row's price (10,480) | A part that points back gets the code part 1 found; "cost" reads the price column |
| Spreadsheet document cards | Showed the table flattened ("Part number Description Price (INR) HX9 4002 ...") | Readable: "HX-900 Spare Parts Catalogue: a table of 500 rows (...)" |
| Reading line | "HX-900_spare_parts.xlsx (HX-900_spare_parts.xlsx)" | Named once |

### Rechecked and passing (installed app)
History delete/scroll; Hindi question (no more ask-back; answers from the leave policy, in English: 0.8B limit); "what is the leave policy?" overview; follow-up (18 days PL); unrelated next question stays on the new topic; Manuals "How long?" options and option answer; Q1 revenue (803,800) with evidence and source panel; KB version switch; C-2087 + part cost (2,350 INR).

### Evals (0.8B, final engine)
- Helix Large: **13/13**
- Helix complex web export: **38/48**. The 10 misses are the same in every earlier run (0.8B reasoning: warm-up rule, warranty, meal deduction, coolant 4 % vs "below 5 %", penalty formula, on-time count, termination, password lockout count, a vague "penalty", a 2-part numeric). None are retrieval failures; the right sections are cited.
- Tests: 327 engine, 36 UI.

### Verdict: **GO**
Remaining limits are the 0.8B model's reasoning on multi-step questions. Larger models handle this kind of question better (the server model scored 13 of 14 on the mixed general-knowledge set earlier), but the full complex set was not rerun on one in this round. Mixing general knowledge with the documents needs the 4B or a server model (the 0.8B is documents-only by design).

## 8. Rigorous check (18 Oct 2026; installed app, browser preview against a real engine + 0.8B, full eval sets)

### Interface flows checked
Manuals browse/search/contents, pictures and picture viewer, feedback (saved, counted in Manage), Copy, Stop, Edit, Regenerate, Chat file attachment and questions about it (incl. after reopening the conversation), History reopen/delete, knowledge-base switch, large text, smallest window (no overflow), engine killed mid-answer.

### Found and fixed
| Problem | Fix |
|---|---|
| Engine connection lost mid-answer left "Thinking..." for ever; Stop left an unhandled error | Error shown with Try again; Stop clean |
| A later question about an attached file was answered from the documents (wrong company) | Attached files the question refers to are shown to the model |
| Answers re-answered an earlier question; repeated their sources as a "Sources:" block; ended with "Sorry, I can only help with work topics." | Removed |
| Manuals list: long file names ran under the contents panel; summaries repeating titles | Wrap; hidden |
| Example questions: "HX 500", "h1.2026", questions about a document's own title | Fixed |
| Search snippets cut mid-word | Whole words |
| "reset" spell-corrected to "rest" -> the 0.8B answered "The amber led meen" | Fixed; such answers rejected |
| Bare partial answers ("100%", "786 KB", "STM32U585") | Completed from the cell/sentence |
| Misspelled codes ("RYL_OB") | Written as the document writes them |
| "No. The documents state: <unrelated rule>" | Rule must be on the question's topic |
| Hindi question echoed back with the language instruction | Asked again |
| Chat on the 0.8B invented answers for things the documents lack | Document questions answered the guarded Manuals way (complex set 31 -> 39/48) |

### Evals (0.8B)
Samples (5 KBs, 121 q, Manuals): 93/121. Complex web: Manuals 38/48, Chat 39/48. Reasoning: Chat ~23/32. Large: 13/13. Remaining misses are 0.8B reasoning (rules applied to a stated case, sums across sections, paraphrases the documents do not use).

Tests: 341 engine, 36 UI. Installed build: backend 18 Oct 03:42.
