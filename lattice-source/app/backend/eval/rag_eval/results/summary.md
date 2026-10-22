# Lattice RAG eval results

## Retrieval (search only, no language model)

Embedding model: embeddinggemma-300M-Q8_0. File: retrieval-20261006-0138.json

### samples: 84 queries; index built in 31.1 s

| Method | Hit@1 | Hit@3 | Hit@5 | Hit@10 | Recall@10 | MRR@10 | nDCG@10 |
|---|---|---|---|---|---|---|---|
| keyword | 0.77 | 0.92 | 0.95 | 0.98 | 0.98 | 0.85 | 0.88 |
| meaning | 0.87 | 0.98 | 0.99 | 1.00 | 1.00 | 0.92 | 0.93 |
| union (app) | 0.77 | 0.99 | 1.00 | 1.00 | 1.00 | 0.88 | 0.90 |

Hit@3 by question type:

| Type | keyword | meaning | union (app) |
|---|---|---|---|
| arithmetic | 1.00 | 1.00 | 1.00 |
| cross-doc | 0.50 | 1.00 | 1.00 |
| fact | 0.95 | 0.95 | 0.97 |
| other-language | 0.50 | 1.00 | 1.00 |
| procedure | 1.00 | 1.00 | 1.00 |
| reworded | 0.60 | 1.00 | 1.00 |
| table | 1.00 | 1.00 | 1.00 |
| typos | 1.00 | 1.00 | 1.00 |
| yes/no | 1.00 | 1.00 | 1.00 |

### emanual: 132 queries, 101 documents; index built in 47.9 s; 389 ms per meaning query

| Method | Hit@1 | Hit@3 | Hit@5 | Hit@10 | Recall@10 | MRR@10 | nDCG@10 |
|---|---|---|---|---|---|---|---|
| keyword | 0.70 | 0.93 | 0.94 | 0.97 | 0.95 | 0.81 | 0.82 |
| meaning | 0.78 | 0.92 | 0.98 | 0.98 | 0.97 | 0.85 | 0.87 |
| union (app) | 0.70 | 0.92 | 0.98 | 0.98 | 0.97 | 0.82 | 0.85 |

### scifact: 300 queries, 5183 documents; index built in 5024.3 s; 810 ms per meaning query

| Method | Hit@1 | Hit@3 | Hit@5 | Hit@10 | Recall@10 | MRR@10 | nDCG@10 |
|---|---|---|---|---|---|---|---|
| keyword | 0.55 | 0.70 | 0.75 | 0.81 | 0.79 | 0.64 | 0.67 |
| meaning | 0.62 | 0.81 | 0.89 | 0.92 | 0.92 | 0.73 | 0.77 |
| union (app) | 0.55 | 0.81 | 0.89 | 0.94 | 0.93 | 0.69 | 0.75 |

## End to end (through the installed engine)

| Run | Model | Questions | Correct (rules) | Read right section | Cited right section | Faithful (judge) | Correct (judge) | Not-in-docs refused | Hallucinated | Median s | p90 s |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 08b-chat-fix | local | 103 | 76% (71/93) | 84% (78/93) | 84% (78/93) | - | - | 50% (5/10) | 5 | 10 | 16 |
| 08b-chat-fix2 | local | 103 | 69% (64/93) | 69% (64/93) | 69% (64/93) | - | - | 60% (6/10) | 4 | 10 | 16 |
| 08b-chat-fix3 | local | 103 | 75% (70/93) | 78% (73/93) | 78% (73/93) | - | - | 60% (6/10) | 4 | 10 | 15 |
| 08b-chat | local | 103 | 73% (68/93) | 67% (62/93) | 66% (61/93) | 65% of 83 | 77% of 83 | 60% (6/10) | 4 | 12 | 22 |
| 08b-emanual | local | 132 | - | 79% (104/132) | 76% (100/132) | 76% of 112 | 34% of 112 | - | 0 | 15 | 23 |
| 08b-manuals-fix | local | 103 | 77% (72/93) | 98% (91/93) | 89% (83/93) | - | - | 60% (6/10) | 4 | 18 | 37 |
| 08b-manuals-keyword | local | 103 | 72% (67/93) | 91% (85/93) | 85% (79/93) | 76% of 90 | 73% of 90 | 40% (4/10) | 6 | 15 | 30 |
| 08b-manuals | local | 103 | 76% (71/93) | 98% (91/93) | 89% (83/93) | 79% of 91 | 77% of 91 | 40% (4/10) | 6 | 35 | 69 |
| 4b-manuals | local | 103 | 97% (90/93) | 100% (93/93) | 100% (93/93) | 98% of 92 | 98% of 92 | 90% (9/10) | 1 | 139 | 376 |

**08b-chat-fix**, correct by question type: arithmetic 75% (3/4), cross-doc 50% (1/2), fact 84% (31/37), follow-up 67% (6/9), other-language 75% (3/4), procedure 75% (3/4), reworded 60% (3/5), table 76% (16/21), typos 0% (0/2), yes/no 100% (5/5)

**08b-chat-fix2**, correct by question type: arithmetic 100% (4/4), cross-doc 0% (0/2), fact 76% (28/37), follow-up 33% (3/9), other-language 100% (4/4), procedure 75% (3/4), reworded 60% (3/5), table 67% (14/21), typos 0% (0/2), yes/no 100% (5/5)

**08b-chat-fix3**, correct by question type: arithmetic 100% (4/4), cross-doc 50% (1/2), fact 81% (30/37), follow-up 56% (5/9), other-language 75% (3/4), procedure 75% (3/4), reworded 60% (3/5), table 76% (16/21), typos 0% (0/2), yes/no 100% (5/5)

**08b-chat**, correct by question type: arithmetic 100% (4/4), cross-doc 50% (1/2), fact 76% (28/37), follow-up 56% (5/9), other-language 100% (4/4), procedure 75% (3/4), reworded 60% (3/5), table 71% (15/21), typos 0% (0/2), yes/no 100% (5/5)

**08b-manuals-fix**, correct by question type: arithmetic 0% (0/4), cross-doc 50% (1/2), fact 84% (31/37), follow-up 78% (7/9), other-language 100% (4/4), procedure 100% (4/4), reworded 40% (2/5), table 76% (16/21), typos 100% (2/2), yes/no 100% (5/5)

**08b-manuals-keyword**, correct by question type: arithmetic 0% (0/4), cross-doc 50% (1/2), fact 84% (31/37), follow-up 67% (6/9), other-language 75% (3/4), procedure 100% (4/4), reworded 20% (1/5), table 67% (14/21), typos 100% (2/2), yes/no 100% (5/5)

**08b-manuals**, correct by question type: arithmetic 0% (0/4), cross-doc 50% (1/2), fact 84% (31/37), follow-up 78% (7/9), other-language 100% (4/4), procedure 100% (4/4), reworded 40% (2/5), table 71% (15/21), typos 100% (2/2), yes/no 100% (5/5)

**4b-manuals**, correct by question type: arithmetic 75% (3/4), cross-doc 100% (2/2), fact 100% (37/37), follow-up 100% (9/9), other-language 100% (4/4), procedure 100% (4/4), reworded 100% (5/5), table 90% (19/21), typos 100% (2/2), yes/no 100% (5/5)

## 4b-manuals vs 08b-manuals on the same 103 questions

| Run | Correct (rules) | Correct (judge) | Faithful (judge) | Not-in-docs refused | Median s |
|---|---|---|---|---|---|
| 4b-manuals | 97% (90/93) | 98% of 92 | 98% of 92 | 90% (9/10) | 139 |
| 08b-manuals | 76% (71/93) | 77% of 91 | 79% of 91 | 40% (4/10) | 35 |
