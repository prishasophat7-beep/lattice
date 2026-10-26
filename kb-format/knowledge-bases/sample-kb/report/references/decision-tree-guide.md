# Refining the decision tree

`interaction/decision-tree.json` is the interview the assistant runs when a request is vague. The
converter writes a **draft** (`"_draft": true`) from the headings: which part of the document, and,
where the manual describes a step in several ways ("Option A / Option B", "Method 1 / Method 2"),
which way. A person who knows the product should turn it into the real interview.

## Shape

```json
{
  "_draft": true,
  "start": "start",
  "nodes": {
    "start": {
      "ask": "What are you trying to do?",
      "options": [
        {"label": "Train a model", "next": "train-type"},
        {"label": "Something else", "next": "open"}
      ]
    }
  },
  "resolve": {},
  "results": {
    "train-classification": {
      "modules": ["image-classification"],
      "summary": "Training an image-classification model",
      "start_hint": "image-classification/03-training",
      "ask_next": []
    }
  }
}
```

- **nodes**: `ask` is the question, as the assistant should say it; `options` are 2-5 answers plus
  "Something else". `next` is another node id, `result:<id>`, or `open` (answer freely).
- **results**: what the interview settled. `modules` (folder names under `modules/`) limit where to
  look; `start_hint` is the section to begin with; `ask_next` lists follow-up questions to ask
  before giving steps.
- **resolve** (optional): variables to fill from earlier answers, e.g.
  `"resolve_keys": ["task", "variant"]` with results keyed `"<task>|<variant>"`.

## Making it good

1. Start from the user's goal ("What are you trying to do?"), not the manual's chapter list.
2. Ask only questions whose answer changes the steps (project type, method, product model).
3. Use the manual's own words in options, so users recognise them.
4. Keep it shallow: two or three questions to a result.
5. Every node needs a way out ("Something else" → `open`).
6. When it is right, remove `"_draft": true` and run `python validate_kb.py`; it checks every `next`
   and `modules` reference and prints every path through the interview.
