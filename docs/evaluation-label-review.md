# Independent evaluation-label review

## Goal

Check whether the authored `fixture-v1` evidence supports the expected root cause, abstention decision, and relevant-evidence annotations. This review is independent only if performed by someone who did not author the fixture labels and who records their judgments before seeing the answer key.

## Blind review materials

Give the reviewer only the normalized evidence bundles in `evals/fixtures/iq-001.json` through `evals/fixtures/iq-024.json` and a blank copy of the review form below. Do not share `evals/cases.jsonl`, `evals/expected_evidence.jsonl`, `scripts/author_eval_fixtures.py`, benchmark outputs, or this repository's authoring notes until the review is locked; those files disclose expected answers or how the evidence was constructed.

Each bundle includes the incident window and observations, but not the expected cause or relevance annotations. The input is synthetic and intentionally small. Ask the reviewer to treat each bundle as a standalone incident and avoid inferring answers from file order or prior cases.

## Review procedure

For each case, the reviewer should record:

1. Their best-supported root cause, or `unknown` if the bundle is insufficient.
2. Whether the evidence is sufficient for a confident diagnosis and what important evidence is missing.
3. Evidence IDs that directly support the diagnosis and any that contradict it.
4. Any plausible alternative cause that the evidence does not rule out.
5. A safe, read-only next investigative step.

Have the reviewer sign and date the completed form before comparing it with the answer key. Then compare cause labels, abstention choices, citation relevance, and omitted/conflicting evidence. Record disagreements and the resolution; do not silently change the v1 labels. If changes are warranted, preserve v1 and create a new dataset version and checksum manifest.

This process is a human audit of authored synthetic fixtures. It does not establish performance on real incidents or validate model outputs.

## Blank review form

| Case ID | Independent cause or unknown | Evidence sufficient? | Supporting evidence IDs | Contradicting evidence IDs | Missing evidence / alternatives | Safe next step |
| --- | --- | --- | --- | --- | --- | --- |
| iq-001 |  |  |  |  |  |  |
| iq-002 |  |  |  |  |  |  |
| iq-003 |  |  |  |  |  |  |
| iq-004 |  |  |  |  |  |  |
| iq-005 |  |  |  |  |  |  |
| iq-006 |  |  |  |  |  |  |
| iq-007 |  |  |  |  |  |  |
| iq-008 |  |  |  |  |  |  |
| iq-009 |  |  |  |  |  |  |
| iq-010 |  |  |  |  |  |  |
| iq-011 |  |  |  |  |  |  |
| iq-012 |  |  |  |  |  |  |
| iq-013 |  |  |  |  |  |  |
| iq-014 |  |  |  |  |  |  |
| iq-015 |  |  |  |  |  |  |
| iq-016 |  |  |  |  |  |  |
| iq-017 |  |  |  |  |  |  |
| iq-018 |  |  |  |  |  |  |
| iq-019 |  |  |  |  |  |  |
| iq-020 |  |  |  |  |  |  |
| iq-021 |  |  |  |  |  |  |
| iq-022 |  |  |  |  |  |  |
| iq-023 |  |  |  |  |  |  |
| iq-024 |  |  |  |  |  |  |

Reviewer:

Date review was locked:

Disagreements and adjudication notes:
