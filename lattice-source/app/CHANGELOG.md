# Lattice changelog

The version is set in `VERSION` (`set-version.ps1 <version>`). Every release build is kept as
`Lattice-share\builds\Lattice-<version>-setup.exe`, listed in `Lattice-share\builds\BUILDS.md`.
Numbering: the last number for fixes (0.2.0 -> 0.2.1), the middle one for new features (0.3.0).

## 0.2.7 - 28 Oct 2026

Found by the last overnight runs (0.2.6, the second question set)
- Tables: an amount written the Indian way ("8,40,000" INR) was read as 8, so a ranking, total or difference over
  an order-value column was wrong. It is read as 840000, as the figure check already did.
- Tables: a date ("27 May 2026") was read as the number 27: "Which purchase order had the biggest delay?" was
  answered "PO-26-0538 has the highest due date (27)" instead of the model's right PO-26-0503 (14 days late).
  Dates are not amounts.
- A ranked value carries its column's unit: "the highest order value (920,000 INR)", not "(INR) (920,000)".

## 0.2.6 - 28 Oct 2026

Found by the overnight tests (a second set of generated questions, two-turn conversations, MiniMax M3.1 Flash
as a second model, Nemotron Bolt E4B as a stand-in for the 4B in Manuals mode)
- Chat: a rate-limited model server was reported as "busy or still loading" (Manuals said "rate-limited").
- A rate-limited model server (HTTP 429) is waited out longer before Lattice gives up (5 s, then 10 s; it was
  2 s, then 4 s): on a rate-limited endpoint half the answers of an overnight test failed with "try again".
- Chat: "What does PL stand for?" was answered right ("PL stands for Privilege Leave", from the leave policy's
  annexure) and shown as "The documents on this computer don't cover that" 3 times in 4: the question's
  subject was taken to be "stand", which no document has. "Stand for", "full form", "abbreviation" are asking
  words, and an acronym of two capitals (PL, HR, QA) is the subject.
- Manuals: "What about the most up-to-date way to find manager and office location details?" was answered
  "Learning budget has the highest details (1,500)": "most" was read as a ranking of a table's "Details"
  column. A ranking needs what it ranks named right after "highest", "most" ... ("the highest total downtime",
  "the most expensive part").
- A number the document writes as a word ("Six people clicked the link; one entered their password") checks out
  as a figure: an answer saying "1 employee entered their password" was held back as not checking out.
- "Can I book a flight less than 7 days before travel without manager approval?" was refused as a booking
  request ("I can't book tickets") in both modes. Only requests to the assistant ("Book me a train ticket",
  "can you book a cab") are refused; questions about the travel policy are answered.
- Manuals: "Did Arvind Metals meet its on-time delivery target in Q2 2026?" answered right by MiniMax ("No. 4 of
  7 orders were on time (57.1%), below the 97% target") was shown as "The documents don't cover this": its
  sources were narrowed to the one with the 97% target, which does not name Arvind Metals. The sources the
  answer came from are kept.

## 0.2.5 - 27 Oct 2026

Answers that read like a person wrote them, and notes as badges (screens reviewed with real Nemotron answers:
long tables, conversations of 3 to 5 turns, both modes)
- Answers Lattice reads out itself are sentences: "The warranty period for R7 and R9 routers is 1 year longer
  than for M3 mesh nodes (50% longer)" instead of "Worked out from the table: R7 and R9 routers minus M3 mesh
  nodes in Warranty period: 1 (50% of M3 mesh nodes)"; "For Privilege Leave (PL), the carry forward is up to
  45 days" instead of "From the documents: Leave Type: ... · Carry Forward: Up to 45 days"; "No. Contractors
  may not drive Helix forklifts." instead of "No. The documents state: ...". A difference keeps its unit.
- Notes on an answer (cut off, sources not named, only part of a table in the document, steps from different
  parts of the manual) are badges on the card, not italic lines inside the answer. Old answers in History
  show them as badges too.
- Formulas written in LaTeX ("\frac{Output × Target cycle time}{Available time}") are shown as plain text.
- "Check against the source" names the first 6 figures and how many more (one answer listed 17).
- A summary point starting in bold lost its first "**" and showed "12,480 tickets resolved** with ...".
- A summary's "Q3 2026" was flagged although the document's name says it.
- A figure flagged in one answer showed "Numbers match the document" when the next answer repeated it.
- References written "[S5, [4], Notes]" were shown as text (and "S5" flagged as a figure); "[2]. [2]" shows once.
- A busy model server says which: "rate-limited", "still loading" or "not responding" (all were "busy or still
  loading").
- Chat: an answer no longer repeats the question as its first line, nor ends with "Worked out from the tables
  (exact): ...", the label its worked-out tables come under.
- A shouted "NO: The policy states ..." reads "No. The policy states ...".
- "Working in: skylink_limited_warranty.pdf › skylink_limited_warranty.pdf" names the document once.
- The options of a question back are one list of equal width (each was as wide as its own text).

## 0.2.4 - 27 Oct 2026

Found by 176 generated questions over all 9 new-format knowledge bases (Nemotron 3.5 Lightning answering,
GPT-OSS 20B grading; Chat 81/88 and Manuals 74/88 graded correct before these fixes)
- Manuals: a right "Yes. Turn the outer ring to change the target temperature ..." was replaced by an
  unrelated sentence ("The documents state: With Adaptive Recovery turned on ... not after it"); a rule now
  replaces a yes only when it shares two real words with the question (a name such as "X2" does not count).
- Manuals: when the model found nothing, a sentence sharing two words with the question was shown as the
  answer ("Do I need training to run programs?" -> "The documents state: Run the spindle warm-up program");
  it must now hold more than half of the question's words.
- Manuals: "How does the R7 warranty compare to the M3's?" was answered right (3 years vs 2) and then shown
  as "The documents don't cover this", because the R7 has its own troubleshooting guide and the answer came
  from the warranty document. A section that names the product is about it, whichever document it is in.

## 0.2.3 - 27 Oct 2026

Found by testing the app's own screens with real NVIDIA Nemotron answers (new knowledge-base format)
- Chat: an answer to a new question repeated the previous answer ("What is ResNet?" ended with the sick-leave
  days asked before); lines restating the last answer on its topic are removed, and the model is told to
  answer only the latest message.
- Chat: every quote box was drawn as the general-knowledge box, so the manual's own words in one ("What the UI
  shows ...") read as not from the documents; only general knowledge is boxed now. Labels such as "General
  knowledge (brief):" are recognised (they showed "(brief)**").
- Chat: a paragraph labelled general knowledge that holds the documents' figures (a downtime total) keeps the
  label only for its general sentences.
- Chat: a code an answer depends on (part HX9-4471 named by alarm C-2087) brings its lines from another section,
  so the price is read, not guessed.
- Chat: "Write a reply to a customer ..." is written, not summarised with "shall I draft it?".
- A fact read straight from a table was labelled "Worked out ... not stated in these words".
- "How do I add data?" offered only the parts that title the step "Adding data to dataset"; parts with either
  title are offered, in the manual's order (Single X and Multi X first).
- Screens: pictures of a step line up with the step's text; a source or picture whose section is named after
  its document no longer shows the name twice ("leave_policy.pdf › leave_policy.pdf"); a picture that cannot be
  drawn is hidden instead of an empty box.
- "I'm stuck" answers no longer repeat the instruction ("Steps (as a numbered list)").

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
