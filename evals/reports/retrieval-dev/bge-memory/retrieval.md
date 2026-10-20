# IncidentIQ retrieval evaluation

- Corpus: `knowledge-v1` (13 chunks)
- Dataset: `fixture-v1`; split: `dev`
- Embeddings: `BAAI/bge-small-en-v1.5`

| Method | Recall@1 | Recall@3 | Recall@5 | MRR |
| --- | ---: | ---: | ---: | ---: |
| lexical | 0.0938 | 0.2188 | 0.5312 | 0.4969 |
| vector | 0.1406 | 0.3438 | 0.5312 | 0.5406 |
| hybrid | 0.1406 | 0.3906 | 0.5156 | 0.5469 |

Relevance is based on authored synthetic query judgments.
