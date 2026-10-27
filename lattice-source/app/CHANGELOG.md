# Lattice changelog

The version is set in `VERSION` (`set-version.ps1 <version>`). Every release build is kept as
`Lattice-share\builds\Lattice-<version>-setup.exe`, listed in `Lattice-share\builds\BUILDS.md`.
Numbering: the last number for fixes (0.2.0 -> 0.2.1), the middle one for new features (0.3.0).

## 0.2.2 - 27 Oct 2026

- Search: the parts of a hyphenated word were not reduced to their stem like other words, so "late-delivery"
  in a question never matched "delivery" in the documents. They are now.
- Manuals: when the sections read cover the question but the model's figure does not check out against them
  (a wrong sum for the late-delivery penalty), the answer says so instead of "The documents don't cover this".
- Builds: BUILDS.md shows characters such as "·" and "×" correctly.

## 0.2.1 - 27 Oct 2026

Found by testing with NVIDIA Nemotron 3.5 Lightning 30B and the local Qwen 3.5 4B (new knowledge-base format)
- "Write a python script", "book me a train ticket": declined the same way by every model (one model offered
  to write the script). Questions about a tool's Script mode, the travel policy or booking approvals are answered.
- A table in an answer kept its header but lost a row that repeated the sentence above it: table rows are kept.
- A correct total written out as "27,600 + 11,700 + 9,750 = **49,050**" was flagged as not in the documents:
  bold marks no longer hide the sum.
- "What is <product>?": the manual's About section is given whole (a model answered from Configuration screens),
  and is no longer described as "the section you selected".
- A decline that also lists what it can help with was shown as general knowledge.
- Manuals: "What caused the ticket spike and how should agents fix it?" was answered with an unrelated table
  cell ("Tickets resolved · Q3 2026: 12,480") in place of the model's right answer; how/why questions and
  questions in two parts are no longer answered from one cell.
- Manuals: a "SOURCES: A0, A3" list at the end of the answer's last line was shown as part of the answer.
- Chat: a question in Hindi (or another non-Latin script) found nothing and the model answered off topic; it is
  now told to search the English documents with English words and answer in the question's language.
- Chat: a bare answer ("12 days") from the documents was labelled general knowledge; "none of these mention
  ..." answers too.
- "How do I annotate text with method 2?" was asked which method; a way named by its number is followed.
- A total worked out with a count ("1.5 % × 2 weeks = 3.0 %") was flagged as not in the documents.
- Chat: references written with a dash ("[S5 – About]", "(S4 - Wiring)") stayed in the answer as text and the
  answer lost its sources.
- Chat: a closing "Sorry, I can only help ..." paragraph after an answer is removed also when it lists what it
  can help with; a draft signed twice ("[Your Name]" two times) keeps one signature.
- Builds: each entry in BUILDS.md lists the version's changes; a build needs a CHANGELOG section.
- CLAUDE.md: how to record changes, set the version and build (for everyone working on Lattice with Claude).

## 0.2.0 - 27 Oct 2026 (superseded by 0.2.1)

Office knowledge-base format (kb_converter_tool)
- Picture descriptions written as `> - **GLOBAL SCENE**` blockquotes, and kb.json `image_descriptions`,
  are read as pictures, not as the manual's text.
- The decision tree: "I'm not sure yet" options; "SingleX" matches "Single X"; "Type 1 or Type 2?" is
  asked only for questions about that part.
- "What is <product>?" is answered from the manual's own About / Introduction section.
- Manage checks every imported knowledge base and says what Lattice did not understand;
  `lattice-backend.exe --diagnose "question"` reports titles only, for documents that cannot leave a machine.

Any model
- Every model replies in plain text that Lattice reads itself (no "Structured replies" setting).
- Thinking text (`<think>`) is never shown; a tool call written as text is handled; a section is not re-read.
- Test connection checks that the server supports tool calls and says how to fix vLLM if not.

Answers
- "I'm stuck / how to proceed": what the manual says, the steps, then "Which step are you at?".
- Chat declines code, bookings, purchases and off-topic chat, says plainly what the documents do not
  cover, and marks general knowledge.
- A long Manuals answer that reaches the length limit is shown, marked cut off (it was the error "got
  stuck writing").
- Chat mode on its own server model uses that model for its summaries, the option picked after a
  question back, and Next (they ran on the Manuals model, the local 4B).
- No lost lines: Chat kept dropping lines with the word "calculate"; Manuals dropped everything after a
  line starting "Sections:".

## 0.1.0 - up to 27 Oct 2026, 16:13

The builds before versioning (all called 0.1.0). Their changes are listed in the README of the
Lattice-share package.
