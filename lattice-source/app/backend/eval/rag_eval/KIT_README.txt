Lattice RAG evaluation kit
==========================

Measures how well Lattice finds the right information and answers from it, on the office machine,
with the network model (Qwen 3.6 27B) and the embedding model Lattice already uses.

What it runs
  1. Retrieval benchmarks (search only, no language model): the 4 sample knowledge bases (103 labelled
     questions) and public datasets: RAGBench EManual and TechQA, MultiHop-RAG, BEIR SciFact and
     NFCorpus (and FiQA, if asked). Metrics: Hit@1/3/5/10, Recall@10, MRR@10, nDCG@10, comparing keyword
     search, search by meaning and Lattice's combined search.
  2. End to end through the installed Lattice engine: the 103 labelled questions in Manuals and Chat
     mode, and a sample of each public Q&A dataset (EManual, TechQA, MultiHop-RAG) in Manuals mode.
     Scores: correct facts, right section read and cited, "not covered" for questions outside the
     documents, made-up answers, time per answer.
  3. A judge (the same model) grades each answer: faithful to its sources, and correct.

Before you start (office machine)
  - Lattice installed from the installer in the share folder (built 6 Oct 2026, 22:29, or later:
    earlier builds cannot read the current knowledge-base export format), with the embedding model imported
    (Manage > Search by meaning). Without it, only keyword search is measured.
  - Python 3.11 or later (python.org). The first run installs the engine's Python packages into
    app\backend\.venv-eval, which needs internet access (about 100 MB).
  - The model server's URL and the model name exactly as the server lists it.

Run (PowerShell, in this folder)
  $env:QWEN_API_KEY = "<key>"      # only if the server needs a key; never printed
  powershell -ExecutionPolicy Bypass -File app\backend\eval\rag_eval\run_office.ps1 `
      -Endpoint http://<server>:8000/v1 -Model <model name>

  Quick check of the setup first (2 questions, a minute or two):
      ... run_office.ps1 -Endpoint ... -Model ... -Steps samples -Only a01,k03

  Options: -Steps retrieval,samples,public,judge,report   -Limit 60 (questions per public dataset)
           -Sets samples,emanual,techqa,multihop,scifact,nfcorpus (add ,fiqa for the 57,638-document
           set: several hours of indexing on a CPU)
  An interrupted run continues where it stopped: run the same command again.

Results
  app\backend\eval\rag_eval\results\summary.md   the overview (tables)
  app\backend\eval\rag_eval\results\*.json       every question, answer, source and score
  app\backend\eval\rag_eval\laptop-results\      the same eval on the development laptop with the
                                                 built-in 0.8B and 4B models, for comparison
  Bring the results folder back; delete the work folder (it holds the engine's settings, including
  the API key, as Lattice itself does).

Notes
  - A model grading its own answers is lenient: read the judge's faithfulness as an upper bound.
  - Public datasets: BEIR (SciFact, NFCorpus, FiQA), RAGBench (Galileo), MultiHop-RAG (Tang & Yang,
    2024); see each dataset's licence on Hugging Face before publishing results.
