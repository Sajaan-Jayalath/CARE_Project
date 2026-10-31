# Stage 6 — document-level representation and role patterns

The document pipeline now performs a separate deterministic pass after chunk
reconciliation. It aggregates validated NLP evidence across **all successfully
processed windows**, without another model call or external API.

## What it returns

`analysis.document_analysis` contains:

- `status`: completed, partial, unavailable or failed.
- `assessment`: insufficient evidence, isolated example, multiple examples with
  no threshold pattern, repeated pattern, or unavailable.
- `observations`: explicit reference, role family, example/source-unit identity,
  exact evidence and the supporting NLP relationship IDs.
- `statistics`: explicit-gender example counts, separate pronoun-only counts,
  technical/leadership/supporting counts, and source sections without supported
  observations. These sections are unknown, not evidence of gender absence.
- `findings`: document-pattern findings under Representation or Professional
  Roles, each with multi-example evidence, explanation, recommendation and review
  reasons. Their scope/source distinguish them from passage-level flags.
- `excluded_observations`, `coverage`, `policy`, and explicit limitations.

Passage findings remain in `analysis.findings`. Its existing summary counts only
those passage findings. The frontend must display document-pattern findings as a
separate section with links to all supporting passages, and must not present the
sum as a proven number of distinct bias issues. One pattern can span many local
findings. The original source mapping from Stages 4–5 still applies to all evidence.

## Counting and pattern policy

The initial, configurable function defaults require at least **three distinct
examples/source units** and at least **80% dominance** within the observed
reference-group appearances for a representation/role-family candidate. These
are conservative engineering thresholds, not validated research accuracy metrics.

For example, three explicitly male technical examples can trigger a representation
review candidate. Three male technical examples paired with three female support
examples have balanced overall visibility but can trigger a Professional Roles
pattern. Balanced gender participation across the same role family does not meet
the threshold. One legitimate person in a role does not establish a document pattern.

Explicit `Example 1`, `Scenario A`, and `Case study 2` labels group subsequent
paragraphs until another label. Otherwise paragraphs/PDF blocks/table rows are
source-unit proxies and findings say so. Repeated identical sentences count once,
including copied content and overlapping chunks. Several relationships in one
example do not increase its count for the same group/role. Group counts can overlap
when an example contains several explicit reference groups; their sum is not a
count of unique examples or people. A recurring person is not entity-resolved
across the document, so the result never claims unique-person statistics.

Supported explicit word families include woman/women/female/girl/mother,
man/men/male/boy/father, and nonbinary/non-binary. No names, titles or neutral
pronouns are assigned a gender. `he/him` and `she/her` occurrences are retained
as **pronoun-only** observations, separately from gender-word counts, without
inferring gender identity or using them to assert a gender imbalance. Transgender
alone is not treated as a separate gender or mapped to men/women.

## Role and context handling

Role nouns use the existing grammatical P1 gender–occupation relationships.
Role families include engineering/programming, leadership/management and
administrative/support work. Compound roles use their occupational head:
`assistant manager` is leadership, `engineering manager` is leadership.

P6 actions now retain their own grammatical direct object. This supports patterns
such as leading software teams, developing code, taking notes or organising
meetings. A nearby role word in another person's clause cannot supply the object.
The role vocabulary is finite and deliberately does not classify every activity.

Negated relationships, generic/prescriptive statements and likely critical,
historical or research discussion are excluded from example counts and listed
with their evidence/reason. Headers, footers and speaker notes are excluded from
the document example count to reduce duplicated/supplementary material. They may
still receive local passage analysis. These contextual filters are conservative
heuristics: a paragraph containing both a critical discussion and a real example
may require manual review. Exclusion is not a verdict that the text is unbiased.

Every document finding requires contextual review. Default Medium severity is a
review-priority policy and Low confidence communicates unvalidated interpretation;
neither is a measured probability. Counts identify patterns in observed material,
not demographic population proportions or proof of discrimination. Factual
historical identities must not be rewritten merely to balance a count.

## Integration and failure behaviour

The existing `/api/analysis/text`, `/api/analysis/file` and document CLI run this
pass automatically. No new model, package, training dataset or API key is needed.

```powershell
.\.venv\Scripts\python.exe -m backend.extraction --file sample_data/text/document_patterns.txt --analyse --output output/document-pattern-result.json
.\.venv\Scripts\python.exe -m pytest tests -q
```

An unavailable NLP layer produces an unavailable document assessment, not a claim
of balanced representation. Missing NLP segments or extraction warnings make the
document assessment partial; detected patterns retain an incomplete-coverage
warning. A failure of this pass preserves existing passage findings and marks the
overall analysis partial. LLM failure does not erase successful NLP aggregation.

Direct short-document `backend.ai.analysis` and the standalone LLM CLI
remain lower-level tools; use the extraction/document pathway for this whole-
document pass. No OCR/visual document analysis is introduced here.

## Verification

Full regression suite: **200 tests passed** (180 existing tests plus 20 Stage 6
cases). The two warnings are existing Starlette test-client deprecations.

Tests exercise real local spaCy extraction and deterministic aggregation: isolated
and balanced examples; repeated technical/leadership/supporting roles; actions
and direct objects; names-only input including the surname Man; pronouns;
nonbinary spelling variants; negation and critical/research context; identical
duplicates; example boundaries; partial NLP coverage; integration and failure
isolation. The live local run in ignored `output/stage6-live.json` used two windows
and found balanced overall gender-word counts (3/3), with one Professional Roles
pattern for technical/supporting division. It did not invent a numerical
Representation imbalance.
