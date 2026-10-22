# Agent harness proposal: reasoning over documents with Qwen3.5-4B (local) and Qwen 3.6 27B (remote)

Status: design proposal, 14 Oct 2026. No code has been changed. File references are to
`app/backend/oasis_engine/` unless stated otherwise.

## 0. Summary

The current harness was shaped by the 0.8B model: it keeps the model away from decisions (fixed
pipeline, presearch, tiny snippets) and then repairs or vetoes what the model writes with regex and
word-overlap guards. With 4B and 27B the bottleneck moves: the models can reason, but the harness gives
them a flat view of the documents, no way to compute over tables, no way to record *why* they believe
something, and guards that turn legitimate inferences into "not covered".

Five recommendations, in priority order:

1. **An evidence ledger** (`note` tool + structured final answer with per-part status
   `stated | worked_out | not_covered`). Citations, number checks, "not covered" and "worked out from"
   are decided from verified notes, not from regexes over the prose. This replaces most post-hoc guards.
2. **Plan first**: one structured call that rewrites the question, splits it into sub-questions, and
   flags ambiguity. Retrieval runs per sub-question; the answer must account for every part.
3. **Structure-aware tools**: a document map with table schemas, `read` that returns a section's own
   text (not its whole subtree), `query_table` (filter/sort/aggregate in Python), and `find_facts`
   (limits, amounts, deadlines, conditions about a topic, at sentence level).
4. **Two drivers over one engine**: a fixed 2-call pipeline for the 4B CPU tier (in both modes), a
   bounded tool loop for 27B. Same tools, ledger, verification and rendering underneath.
5. **New eval sets** (vague, inferential, multi-part, compare/compute, follow-up chains, partial
   coverage) in the existing `questions_*.json` format, with part-level and clarify metrics.

## 1. Diagnosis: where the harness limits reasoning

### 1.1 What the model sees about the documents

- **A flat list, not a map.** Chat instructions list up to 40 documents with one description each
  (`chat.py:403-411`). Section structure is only available through `manual_outline` (`chat.py:513-532`),
  one document at a time, capped at 8,000 chars. Tables are invisible until read: the bundle merges a
  table's summary into its section summary (`docs/kb-bundle-spec.md`, "type: table"), so the model
  cannot tell that "Working Hours and Leave" holds a 6-row leave table with an "Allowance" column.
- **Search results are 220-char windows** (`search.py:161`, via `hit_rows`, `chat.py:355-362`) with no
  section summary and no "this section has a table / has children" flag. A vague question ("what is
  the limit?") gets windows chosen by query-word density, which is nearly random for a two-word query.
- **Reading a section reads its subtree.** `Doc.section_text` concatenates all descendants for Markdown
  documents (`models.py:94-96`). Reading "1 Welcome" or a document's title root returns most of the
  document, truncated at 6,000 chars (`chat.py:49`, `chat.py:451`). This is also the root of the
  "parent Introduction cited" problem: parents contain their children's words, so word-overlap
  citation recovery (`_implied_guess`, `chat.py:870-904`) and the evidence picker prefer them.
- **Tables are dumped whole.** `read_table` serialises every table in the section as JSON up to 200 rows
  from 60,000 chars of text (`chat.py:455-471`, `tables.py:11`). For a 4B model with an 8,192-token
  context (`desktop/src-tauri/src/lib.rs:36`), one call can fill the window; for any model the
  comparison itself is still done "in the head" — `calculate` only helps after the model has already
  chosen the rows. The 0.8B wrong 4-row comparison is the visible symptom.

### 1.2 Presearch and decomposition

- `_with_presearch` (`chat.py:750-805`) searches the *whole* question once and injects 3 hits
  (`PRESEARCH_HITS`, `chat.py:148`); Manuals selects at most 3 sections (`pipeline.py:41`) from 16,000
  chars of excerpts (`pipeline.py:62`). A two-part question ("which software needs a request **and** which
  leave needs approval") retrieves for whichever half dominates the BM25 score; the other half never
  reaches the model. Nothing checks that every part was answered.
- Follow-ups: Chat receives the last 6 turns with answers cut to 1,500 chars (`chat.py:50-51`,
  `chat.py:372-378`) but **not which sections those answers came from**; refs (`S1`, `S2`) are renumbered
  every turn (`ChatDeps.refs`, `chat.py:314`). Manuals passes the previous citation (`pipeline.py:756-762`);
  Chat does not.

### 1.3 Loop and budgets

- `MAX_REQUESTS = 8` (`chat.py:52`). When it is hit, everything gathered is discarded and a canned
  "couldn't find a clear answer" is returned (`chat.py:656-659`). Multi-hop questions are exactly the
  ones that need the 7th and 8th step.
- No planning or reflection step: the model goes straight from question to tool calls; there is no
  place to state an assumption, list sub-questions, or decide to ask back.
- 4B on CPU: a tool loop re-sends the growing conversation each step. Instructions (~1.2k tokens) +
  history (up to ~2.5k) + presearch + each `read_section` (~1.5k) exceeds 8k in 3-4 steps, and prompt
  processing dominates latency (4B Manuals: median 139 s, p90 376 s, `eval/rag_eval/results/summary.md`).
  Thinking is off locally (`lib.rs:426`), so the 4B has no hidden scratch space either.

### 1.4 Instructions and guards that block inference

- Manuals: "using ONLY the provided excerpts ... never guess" (`pipeline.py:242-245`) and `_no_answer`
  (`pipeline.py:187-194`), which marks any answer whose *first sentence* says the documents do not state
  something as not found "even when a guess follows". A correct inference — "The handbook doesn't say
  this directly, but parental leave must start within 12 months of birth, so at 14 months you are no
  longer eligible" — is discarded.
- Chat: the same first-sentence test (`_NOT_COVERED`, `chat.py:169-172`, applied at `chat.py:706-711` to
  any answer under 700 chars) replaces worked-out answers with "The documents on this computer don't
  cover that."
- `made_up` (`support.py:60-64`) vetoes an answer when the question's topic words are absent from the
  documents and under 80% of the answer's words appear in what was read. Inferential questions are
  phrased in the user's words ("my daughter is 14 months old"), and the reasoning adds words — the
  guard cannot tell inference from invention. Conversely it only runs when the answer cites something
  or the model is documents-only (`chat.py:713-714`): the 4B is *not* documents-only (`api.py:74`, < 3B),
  so uncited invented password-reset steps pass as "general knowledge" (`chat.py:733`).
- Citation and evidence choice by shared numbers: Manuals keeps only cited sections that share a figure
  with the answer (`pipeline.py:885-889`), and `evidence.quotes` weights a shared figure 3x a shared word
  (`evidence.py:134`). "10" in "10 days sick leave" and "10:00 core hours" look alike.
- Clarification exists only for *documents* that tie (`pipeline.py:778-800`; `ask_which_manual`,
  `chat.py:567-578`). There is no route for in-document ambiguity ("what is the limit?" — gifts USD 100,
  PTO carry-over 5 days, sick leave 3 days without a note, learning budget USD 1,500).

The guards were the right answer for 0.8B. For 4B/27B the fix is not more regexes but giving the model
a structured way to say *what it found, where, and what it concluded from it*, and checking that.

## 2. Proposed tools and exposure

Token budgets assume ~4 chars/token. "4B" = local Qwen3.5-4B, 8k context, CPU. "27B" = remote vLLM,
native tool calls, ≥32k context.

### T1. `doc_map(document?: str, section?: ref, depth: int = 2)` — replaces the 40-line list and `manual_outline`
- **Purpose:** the document's structure as the model's primary navigation aid.
- **Returns:** per document: title, description, then a tree of `[ref] title (locator) — summary`
  (summary ≤ 160 chars), with markers: `▦ table: Leave type | Allowance | Notes (6 rows)`,
  `▣ figure: caption`, `+4 subsections`. Without `document`: one line per document plus its top-level
  sections. Budget: 1,500 tokens (27B), 700 (4B, depth 1, summaries ≤ 80 chars).
- **Refs are stable** for the conversation (see T7), so `S12` in turn 3 is the same section as in turn 1.
- **Tier:** both. For 4B it is not a tool but the fixed prefix of the planning prompt (cached by
  llama-server across questions, since it only changes when the bundle changes).
- **Build:** at import time, from `Section.summary`, `markdown_tables()` headers and `image_notes`; stored
  with the bundle, so it costs nothing per question.

### T2. `search(query, document?: str, k: int = 6)` — merges `search_manuals` and `semantic_search`
- **Purpose:** one search; the harness does keyword+meaning `union` (`embeddings.py:189`) itself.
  Two search tools with overlapping purposes is a choice the model gets wrong and pays a step for.
- **Returns:** leaf-preferring hits (a parent is dropped when a child of it is also a hit), each:
  `ref, document, path, summary (≤120 chars), best passage (≤320 chars), has_table, has_figure`.
  Budget ≤ 700 tokens.
- **Tier:** both (4B: called by the harness per sub-question, not by the model).

### T3. `read(ref, part?: "own" | "all", around?: str)` — replaces `read_section`
- **Purpose:** read exactly one section's own text by default (`Section.own_text`), with a list of its
  children (`ref + title + summary`) so the model can descend. `around="carry over"` returns the
  ±600 chars around the best match within the subtree. Budget ≤ 1,200 tokens per call; longer text is
  paged ("continue with part=2").
- **Effect on citations:** a parent is only cited if its own text was read, which removes the
  "Introduction cited" problem at the source.

### T4. `query_table(ref, table: int = 0, where?: list, columns?: list, sort?: str, limit: int = 20, aggregate?: dict)`
- **Purpose:** filter, sort, aggregate in Python; the model states the operation, the harness does it.
- **Example args:** `{"ref":"S7","where":[["Change","<",0]],"columns":["Category","Change"]}`;
  `{"ref":"S7","aggregate":{"sum":"FY2027 Budget ($K)"},"where":[["Category","!=","Total"]]}`.
- **Returns:** the table title, column headers with units parsed from headers (`$K`, `%`, `days/year`),
  ≤ 20 matching rows, and computed values. Numeric parsing handles `1,870`, `+31.0%`, `18-27 days/year`
  (range → min/max). Every computed value goes to `deps.computed` so `unverified_numbers`
  (`numbers.py:107`) accepts it. Budget ≤ 600 tokens.
- **Cross-document compare** is two `query_table` calls plus `calculate`; no separate `compare` tool.
- **Tier:** 27B as a tool. 4B: the planning step may emit a `table_ops` list that the harness runs
  before the answer step (see §3.2) — a 4B can fill a JSON spec reliably; it cannot reliably compare
  rows read as prose.

### T5. `find_facts(topic, kind: "limit" | "amount" | "deadline" | "condition" | "definition" | "any" = "any")`
- **Purpose:** answer vague and "how long / how much / am I allowed" questions by scanning **sentences
  and table rows** (the units `evidence.py` already splits into) for figures with units or limit words
  (`at least`, `up to`, `within`, `no more than`, `must`, `not allowed`, `requires approval`), near topic
  terms (keyword + meaning).
- **Returns:** ≤ 8 facts: `ref, path, sentence-or-row (≤ 200 chars), kind`. Grouped by section, so the
  harness can see whether the answer candidates come from one topic or several. Budget ≤ 600 tokens.
- **Tier:** both. It also drives in-document clarification (T8) without any model call.

### T6. `note(claim, ref, quote)` — the evidence ledger (scratchpad + verify in one)
- **Purpose:** the model records each fact it will rely on, with the exact supporting text.
- **Harness check:** `quote` must occur in the section (normalised whitespace/case, ≥ 0.9 fuzzy match
  over a sentence or table row). Reply: `N3 ok` or `not found in S7 — nearest: "..."`. Verified notes
  carry their quote into the answer's evidence panel (replacing the guess in `evidence.quotes`).
- **Returns:** ≤ 60 tokens. Notes persist in the conversation state (T7), so a follow-up can reuse them.
- **Tier:** 27B as a tool. 4B: the structured answer itself carries `premises: [{ref, quote}]` and the
  harness verifies them after the call (no extra round trip).

### T7. Conversation state (harness, not a tool)
Injected as a short block before the question each turn (≤ 400 tokens):
```
Conversation so far:
- Q1 "How many weeks of parental leave?" -> answered from S4 (Working Hours and Leave): 16 weeks [N1]
- Q2 "Is it paid?" -> S4: fully paid [N2]
In focus: parental leave (employee_handbook_2026.pdf)
```
Stable refs and notes across turns; Manuals' `standalone_question` (`pipeline.py:726`) generalised to
both modes. The raw history (`history_messages`) shrinks to 2 turns with answers ≤ 600 chars.

### T8. `ask_user(question, options: list[{label, ref?}])` — generalises `ask_which_manual`
- **Purpose:** ask back for in-document ambiguity, not only between documents. Options may name
  sections, interpretations or values ("Gifts from customers", "Sick leave without a doctor's note").
- **Policy (enforced by the harness):** allowed only on a first question or a topic change, at most once
  per turn, and only when `find_facts`/search return candidates from ≥ 2 sections with different
  subjects and the plan marked the question ambiguous. Otherwise the model must answer the most likely
  reading and state the assumption ("Assuming you mean ..."), offering the alternatives in one line.
- **Tier:** both; on 4B the harness decides from T5's grouping, the model only words the options.

### T9. `calculate` — keep, extend
Add date arithmetic (`2026-03-01 + 90 days`, `days between`), and ranges (`18-27`). Inference over
policies is mostly date and threshold arithmetic.

Removed or folded: `search_manuals` + `semantic_search` → `search`; `read_section` → `read`;
`manual_outline` → `doc_map`; `read_table` → `query_table` (with `columns=[]`, `limit=20` it still
returns raw rows). `list_documents`, `find_pictures`, `read_attachment` stay as they are.
Net tool count for 27B: 11 → 10, with far less text per result.

### Example trace (27B): multi-part + inference

Q: *"I joined in March 2024. Can I carry over my unused PTO, and do I need approval to work fully remote?"*

```
plan        -> parts: [P1 PTO carry-over for ~2.5 years tenure, P2 approval for fully remote]
               ambiguity: none
search("PTO carry over")            -> S4 Working Hours and Leave (has_table), S2 ...
query_table(S4, where=[["Leave type","~","PTO"]])
            -> Paid time off (PTO) | 18-27 days/year | Based on tenure; up to 5 days carry over
note("up to 5 PTO days carry over", S4, "up to 5 days carry over")          -> N1 ok
search("fully remote approval")     -> S5 3.1 Remote Work
read(S5)    -> "...Fully remote roles are approved by the department head..."
note("fully remote roles approved by department head", S5, "Fully remote roles are approved by the department head") -> N2 ok
answer      -> parts:
  P1 stated:  Yes, up to 5 unused PTO days carry over [N1]. (Tenure only changes the allowance, 18-27 days.)
  P2 stated:  Yes, fully remote roles need approval from your department head [N2].
```
Six model requests, ~5k tokens of tool output in total.

### Example trace (27B): vague question

Q: *"What is the limit?"* (first turn)
```
plan        -> parts: [P1 unspecified limit]; ambiguity: likely
find_facts("limit", kind="limit") -> groups:
   S9 Code of Conduct: "Do not accept gifts worth more than USD 100"
   S4 Leave table: "up to 5 days carry over"; "No doctor's note needed for up to 3 consecutive days"
   S6 Benefits: "Learning budget USD 1,500 per year"
   IT: "Requests are reviewed by Security within 2 business days" (deadline, dropped: kind=limit)
ask_user("Which limit do you mean?", [Gift value (Code of Conduct), PTO carry-over, Sick days
         without a doctor's note, Learning budget])
```
The same question as a follow-up to "Can I accept a gift from a vendor?" is resolved by T7's
"In focus" line and answered directly (USD 100), without asking.

## 3. Loop design per tier

### 3.1 27B remote: bounded agent loop with a plan and a ledger

```
PLAN     1 structured call  -> {standalone_question, parts[], answer_type, ambiguity, assumptions}
RETRIEVE harness: search + find_facts per part, 3 hits each, shown as one compact block
LOOP     model tools: doc_map, search, read, query_table, find_facts, calculate, note, ask_user
         budget: 12 requests (soft warning at 9: "answer now with what you have")
ANSWER   structured: {parts: [{id, status: stated|worked_out|not_covered|assumed, text, notes: [N..]}],
                      general_knowledge?: str}
VERIFY   harness: every note verified; every figure in text from notes, query_table or calculate;
         every part from PLAN present; worked_out parts list >= 1 note each
RENDER   prose with [1] [2] markers from notes' refs; "Worked out from [1] and [2]:" label;
         not_covered parts in one sentence each; evidence panel = the notes' quotes
```

- **Plan as its own call**, not thinking: it is cheap on vLLM (≤ 300 output tokens), testable with
  `ChatScript`, and its `parts` become the completeness check that fixes dropped halves. With
  `reasoning: true` (`config.py:27`) the 27B may also think inside the loop; keep plan explicit anyway.
- **On budget exhaustion**, do not discard (`chat.py:656-659`): make one final call with tools disabled
  and the ledger in the prompt; parts without notes become `not_covered`.
- **When to stop:** every plan part has a `stated`/`worked_out` note, or a part has had two retrieval
  attempts (search + find_facts / meaning) with nothing relevant → `not_covered`.
- **Stated vs worked out vs not covered.** The model decides; the harness checks:
  - `stated`: the claim is in a verified quote (figures match exactly).
  - `worked_out`: claim follows from ≥ 1 verified notes plus `calculate`/`query_table` results; shown as
    *"Worked out from [1] and [2]: the leave must start within 12 months of the birth; at 14 months it
    no longer can."* Figures may come only from notes or computations.
  - `not_covered`: no relevant note. The answer says what the documents *do* cover nearby, in one
    sentence ("The handbook gives leave allowances but no rule for contractors.").
  - `assumed`: the answer to the most likely reading, with the assumption stated.
- **Guards after this change:** `_NOT_COVERED` / `_no_answer` first-sentence vetoes are dropped for
  structured answers (status is explicit). `made_up` becomes "a `stated` part whose notes do not verify"
  — a precise test instead of 80% word overlap. `only_unchecked_figures` and `unverified_numbers` stay,
  fed by notes + computations. `implied_sources` / `_implied_guess` are unneeded when citations come
  from notes; keep them only for the free-text fallback. The general-knowledge paragraph stays but is a
  separate field, never mixed with document facts; a company-specific procedure (password reset) can
  only be `stated` with notes, otherwise `not_covered` + optional generic advice labelled as such.

### 3.2 4B local CPU: two calls, harness does the walking

The 4B handled follow-ups, comparisons and Hindi when the pipeline fed it; its problem is time.
Prompt processing is the cost, so: **few calls, small prompts, a stable cached prefix.**

```
CALL 1  PLAN+SELECT  prefix (cached): instructions + doc_map(depth 1, ~700 tokens)
                     variable: conversation state (≤300) + question + per-part candidates from
                     search/find_facts (≤ 6 hits, ~600 tokens)
        output: {standalone_question, parts[{id, question, section_refs[≤2]}],
                 table_ops[≤2], ambiguous: bool}
HARNESS              runs table_ops (query_table), reads each selected section with read(around=...)
                     -> excerpts ≤ 2,400 tokens total, split fairly across parts;
                     if ambiguous and find_facts groups ≥ 2 topics: ask_user, stop (no CALL 2)
CALL 2  ANSWER       prefix (cached): answer instructions
                     variable: excerpts + table results + question
        output: {parts[{id, status, text, premises[{ref, quote}]}]}
VERIFY+RENDER        as for 27B
```

- Two model calls on every question (one when the fast path, `pipeline.py:705-718`, hits and the
  question has one part). Prompt per call ≤ 3.5k tokens, well inside 8k, versus up to 6-7k today.
- Put the static part first and identical across questions: llama-server reuses the cached prefix
  (`--cache-ram 512` is already set, `lib.rs:423`); today the instructions embed today's date and the
  attachment list in the system prompt, and the doc list is rebuilt per call — keep those after the
  cached prefix.
- Excerpts by **passage**, not whole section: `read(around=...)` windows instead of 9,000-char
  excerpts (`pipeline.py:48`). This is the largest single latency lever on CPU.
- Chat mode on the 4B should use this driver too, not the free tool loop: the free loop is what
  invented password-reset steps and what takes 11 minutes. General knowledge for 4B stays allowed but
  only as the separate, labelled field.
- Optional third call only for `worked_out` parts that failed verification (one repair attempt).

### 3.3 Common budgets

| | 27B remote | 4B local |
|---|---|---|
| Model calls | plan + ≤ 12 loop + optional final | 2 (+1 repair) |
| Tool result caps | search 700, read 1,200, table 600, find_facts 600 tokens | harness-assembled, excerpts ≤ 2,400 |
| History | state block ≤ 400 + 2 raw turns | state block ≤ 300 |
| Target latency | median ≤ 20 s | median ≤ 60 s on the 8 GB laptop |

## 4. How Manuals mode should relate

Unify the engine, keep two drivers, choose the driver by **model capability**, not by mode:

- **Shared layer:** doc map, `search`, `read`, `query_table`, `find_facts`, ledger verification,
  conversation state, clarify policy, rendering (citations, evidence quotes, figures).
- **Pipeline driver** (§3.2) = today's `Pipeline.ask` evolved: select → answer becomes plan+select →
  answer with parts, premises and table ops. Used for every local model, in both modes.
- **Agent driver** (§3.1) = today's `ChatAgent` evolved. Used for remote models (and local ≥ 14B if
  ever bundled).
- **Modes remain product modes:** Manuals = documents only, always cited, evidence panel, no general
  knowledge; Chat = documents + attachments + labelled general knowledge + drafting. Manuals on the
  27B gets the agent driver with general knowledge off — multi-hop and table questions in Manuals
  deserve the stronger loop too.
- Keep the fast path, smalltalk, fixed replies, picture/summary shortcuts in `api.py` unchanged; they
  are model-independent and fast.

## 5. Evaluation plan

### 5.1 New question sets (existing format, `questions_reasoning.json`)

Same schema as `questions_samples.json` (`id, kb, type, q, facts, gold, gold_any, conv, polarity`),
plus three optional fields added to `common.Question` (`common.py:17-30`):

- `expect`: `"answer" | "clarify" | "not-covered" | "partial"` (default from `answerable`)
- `parts`: list of fact-groups per sub-question (part recall)
- `inferred: true`: the answer is not stated verbatim; must be labelled worked out

| Type | n | Example (sample KBs) | Expected |
|---|---|---|---|
| vague | 12 | acme: "What is the limit?" (first turn) | `clarify`, options include gift value and PTO carry-over |
| vague-in-context | 8 | after "Can I accept a vendor gift?": "what's the limit?" | answer `USD 100` |
| inferential | 15 | acme: "My daughter was born 14 months ago. Can I still start parental leave?" | facts `["no"],["12 months"]`, `inferred` |
| inferential | | acme: "I've been sick 4 days in a row. Do I need a doctor's note?" | `["yes"],["3"]`, `inferred` |
| multi-part | 12 | acme: "Which software must I request in #it-help, and which leave needs approval?" | parts `[["GitHub"],["Figma"]]`, `[["department head","fully remote"]]` |
| compare/compute | 15 | camera: "Which categories shrink in FY2027, and by how much in total?" | `["Travel"],["7"]` (45→38) |
| compare/compute | | acme: "What is worth more per year, the learning budget or the wellness allowance?" | `["learning"],["720"]` |
| cross-doc | 10 | skylink: tickets from failed firmware updates and first fix (existing `k17` style) | both parts |
| follow-up chain | 4 chains × 4 | camera: equipment budget → "why?" → "what does the bench cost?" → "share of the increase?" | each turn's facts; last `inferred` |
| partial | 8 | acme: "How long is parental leave and does it apply to contractors?" | `partial`: 16 weeks + not covered |
| near-miss not-covered | 10 | acme: "How do I reset my Okta password?" (Okta exists, reset does not) | `not-covered`, no steps |

Write them for all four sample bundles (~110 questions), keep 20% held out from development.

### 5.2 Metrics (add to `e2e_eval.py` / `report.py`)

- **Correct (rules)** as today; **part recall** = parts with all facts / parts.
- **Clarify precision/recall**: asked when `expect=clarify`; not asked otherwise (asking on
  `vague-in-context` is a failure).
- **Inference accuracy** and **labelled-as-worked-out rate** on `inferred` questions.
- **False not-covered rate** on answerable questions (today's guard cost), **hallucination rate** on
  `not-covered` and `partial` (today's metric, split by type).
- **Citation precision** = cited sections that are gold or hold a verified note's quote; flags parent
  "Introduction"-style citations. **Unsupported-claim rate** from the ledger verifier.
- **Cost**: model requests, prompt tokens per answer, median/p90 seconds — per tier.
- **Judge** (`judge.py`): add a rubric line "is every worked-out conclusion valid from the quoted
  premises?" Remember the 27B grading itself is lenient; spot-check 30 answers by hand per run.

Run: 4B on the laptop (pipeline driver) and 27B on the office server (agent driver), both modes, before
and after each phase. Unit tests use `ChatScript` / `ScriptedModel` (`tests/conftest.py:66-140`) to pin
plan parsing, ledger verification, budget exhaustion and the clarify policy without a model.

## 6. Phased plan

| Phase | Build | Expected gain | Risk |
|---|---|---|---|
| **1 (1-2 wk)** | Eval set `questions_reasoning.json` + metrics; `read` own-text default; `query_table`; stop discarding on step limit; carry cited refs/notes across turns | Baseline; comparisons; fewer parent citations; follow-ups in Chat | Low. Own-text `read` may miss content that lives only in children → the child list in the result mitigates |
| **2 (2 wk)** | Plan call + per-part retrieval + structured answer with parts/status, in **both** drivers; completeness check | Multi-part questions; explicit not-covered per part | 4B JSON reliability for nested output: keep schema shallow, bounded lists (as `SectionIds`, `pipeline.py:297`) |
| **3 (2 wk)** | Ledger (`note` / premises) + verifier; retire first-sentence vetoes and `made_up` for structured answers; "worked out from" rendering | Inferential answers; precise citations and evidence | Model quotes loosely → fuzzy match threshold tuning; over-permissive inference → judge rubric + hand review |
| **4 (1-2 wk)** | `find_facts` + `doc_map` with table/figure markers + `ask_user` policy | Vague questions; better navigation | Asking too often is worse than a stated assumption: cap at one ask, never on follow-ups resolved by state |
| **5 (1 wk)** | 4B latency: cached prefix, passage excerpts, 2-call budget, Chat on 4B uses pipeline driver | 4B median toward ≤ 60 s | Memory paging on 8 GB is partly outside the harness (model + embedder + OS) |

Cross-cutting risks:
- **Guard regressions.** The current guards encode many real failures (see the comments throughout
  `chat.py`). Remove them only behind the structured path, and keep them for any free-text fallback;
  the eval's hallucination and near-miss sets must not get worse.
- **Two drivers drift apart.** Mitigated by sharing tools, schema, verifier and renderer; the drivers
  differ only in who chooses the next read.
- **27B availability.** If vLLM is down, Chat falls back to the 4B pipeline driver, not to a degraded
  agent loop.
