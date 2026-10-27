# Lattice changelog

The version is set in `VERSION` (`set-version.ps1 <version>`). Every release build is kept as
`Lattice-share\builds\Lattice-<version>-setup.exe`, listed in `Lattice-share\builds\BUILDS.md`.
Numbering: the last number for fixes (0.2.0 -> 0.2.1), the middle one for new features (0.3.0).

## 0.2.0 - 27 Oct 2026 (launch build)

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
