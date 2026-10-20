# IncidentIQ retrieval evaluation

- Corpus: `knowledge-v1` (13 chunks)
- Dataset: `fixture-v1`; split: `dev`
- Embeddings: `hash-ngram-v1`

| Method | Recall@1 | Recall@3 | Recall@5 | MRR |
| --- | ---: | ---: | ---: | ---: |
| lexical | 0.0938 | 0.2188 | 0.5312 | 0.4969 |
| vector | 0.0781 | 0.2969 | 0.5104 | 0.5417 |
| hybrid | 0.1094 | 0.3594 | 0.4688 | 0.5677 |

Relevance is based on authored synthetic query judgments.
