# IncidentIQ retrieval evaluation

- Corpus: `knowledge-v1` (13 chunks)
- Dataset: `fixture-v1`; split: `dev`
- Embeddings: `hash-ngram-v1`

| Method | Recall@1 | Recall@3 | Recall@5 | MRR |
| --- | ---: | ---: | ---: | ---: |
| lexical | 0.1719 | 0.3438 | 0.5312 | 0.5990 |
| vector | 0.0781 | 0.2969 | 0.5104 | 0.5417 |
| hybrid | 0.1875 | 0.3125 | 0.4844 | 0.6354 |

Relevance is based on authored synthetic query judgments.
