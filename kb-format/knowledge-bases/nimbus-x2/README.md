# nimbus-x2

Interactive knowledge base for **Nimbus X2 Product Knowledge Base**, built from the user manual.

## Use it in Lattice

Zip the contents of this folder (`kb.json` at the top of the zip) and import it in Lattice:
**Manage → Import bundle...**. Lattice reads the sections, asks the clarifying questions from
`interaction/decision-tree.json` and gives the model `AGENTS.md` and `needs-review.md`.

## Use it with another agent

Point the agent at this folder and make `AGENTS.md` its instructions; it reads the files directly.

## Layout

```
AGENTS.md                      agent instructions (hand-maintained)
README.md                      this file
index.md                       module list (generated)
needs-review.md                known gaps (hand-maintained; auto-detected block rewritten by builds)
interaction/decision-tree.json clarifying interview (hand-maintained)
modules/<module>/README.md     module overview (generated)
modules/<module>/NN-<slug>.md  one section of the manual (generated)
kb.json                        machine-readable index for search (generated)
images/                        pictures, page_NNN_imgK.png (generated)
pages/                         page screenshots, page_NNN.jpg (not produced yet)
```
