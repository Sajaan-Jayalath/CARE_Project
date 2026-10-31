# Stage 3: result reconciliation

Implemented 2026-09-30 in `backend/ai/analysis/result_merger.py`; automatically
called by `CAREEngine`. No additional model calls, cloud services or dependencies.

## Output

The existing `layers` object retains original rule findings, NLP relationships,
LLM findings, errors, timing and coverage. The root now also provides:

- `findings`: reconciled candidate concerns with stable IDs, evidence, contributing
  sources, original contributions, display explanation/recommendation, severity,
  confidence and review reasons.
- `notices`: potential duplicate overlaps, category differences, assessment
  conflicts, unconfirmed rule findings and incomplete-analysis notices.
- `summary`: counts by category, severity, confidence and review status. The counts
  refer to reconciled candidates, not guaranteed distinct or confirmed bias issues.
  Ambiguous duplicates remain counted and visibly marked for review.
- `reconciliation_status`, `reconciliation_version`, and an optional structured
  `reconciliation_error`.

## Duplicate policy

1. Within a layer, collapse only identical assessments (same source, category,
   evidence, explanation, recommendation, severity and confidence). Keep all
   original contributions, including repeated submissions, for traceability.
2. Across rules and LLM, require the same category, source section and exact or
   containing evidence ranges. Every evidence range on both sides must participate.
3. Merge one-to-one only if each finding has exactly one candidate counterpart.
   Preserve ambiguous overlaps as `possible_duplicate` (see step 4 for rule
   hits contained in a single LLM finding).
4. After one-to-one matching, a rule-only finding whose evidence lies wholly
   inside exactly one same-category LLM finding is folded into it (added
   2026-10-02). One passage-level wording issue then appears once instead of
   once per word (`he`, `his`, `businessman`). The rule evidence stays highlighted
   and its original assessment stays in `contributions`. Hits covered by no LLM
   finding, or by more than one, remain separate.
5. Distinct occurrences and categories remain separate. Same-category overlaps
   with different assessments are not silently deleted.

This is a conservative evidence-matching policy, not a semantic-equivalence
oracle. Educators must review ambiguous cases. The contextual LLM explanation is
preferred for display when merged, but every original explanation, recommendation
and assessment remains accessible. Evidence is not rewritten or fuzzy-matched.

## NLP support

Attach relationships only where evidence overlaps in the same source section and
the relationship type is relevant:

| Finding category | Related NLP types |
| --- | --- |
| Gender Stereotypes | P2 ability, P3 attributes, P4 interest, P5 evaluation |
| Professional Roles | P1 roles |
| Representation | P6 local actions/outcomes |
| Gendered Language | No automatic relationship mapping in the current extractor |
| Visual Representation | No NLP attachment; the text extractor does not inspect images |

The attachment is contextual support, not confirmation of bias or endorsement.
P6 alone cannot establish document-level imbalance. Relationships never generate
new concerns or raise confidence. A negated attached sentence adds a context-review
reason; it does not automatically dismiss or confirm a concern. Unattached
relationships remain available in the raw NLP layer output.

## Differences, disagreements and review

- Preserve a rule finding even if the LLM is silent. If LLM coverage includes the
  evidence but no unique match is returned, mark `unconfirmed_by_llm` and require
  contextual review. An absent finding is not an explicit rejection.
- Different categories sharing evidence are retained with a `category_difference`
  notice. Multiple categories can be independently valid; this does not establish
  a contradiction.
- Explicit conflicts between supplied severity/confidence assessments are recorded
  as `assessment_conflict`, with original values retained.
- The current layer contracts do not emit explicit negative judgements for each
  candidate. Reconciliation cannot invent a semantic disagreement from silence.
- All outputs remain potential concerns. Rule-only findings, conflicts, ambiguous
  overlaps, low confidence, negated support and incomplete analysis receive
  `needs_contextual_review`. Nothing is automatically educator-approved.

## Severity and confidence policy

- Keep supplied severities. If merged contributors disagree, use the highest
  supplied severity for triage and expose the conflict for review.
- Rule-only Gendered Language findings without severity receive a documented Low
  wording default. This does not measure their contextual impact. Otherwise leave
  absent severity unassessed.
- Retain a supplied qualitative confidence without an agreement bonus. If supplied
  values conflict, choose the lowest and record the conflict. If none is supplied,
  confidence is null/Unassessed, not an invented probability.
- Summaries include Unassessed buckets. These policies concern prioritisation;
  the combined system still requires independent accuracy evaluation.

## Partial and failed results

Failed/skipped layer contributions never enter the reconciled findings. If any
layer failed or skipped content, retain successful findings and mark both the
pipeline and reconciliation partial. Include an incomplete-analysis notice even
if no findings were returned. If all layers fail, the pipeline remains failed,
reconciliation is not performed, and summary is null.

If reconciliation itself fails, return the original layer outputs, an explicit
reconciliation error and partial/failed overall status. Do not report a successful
zero-findings summary. Source validation still occurs before reconciliation.

## Run and verify

```powershell
.\.venv\Scripts\python.exe -m backend.ai.analysis --text "Every programmer should submit his code." --output output/care_result.json
.\.venv\Scripts\python.exe -m pytest tests -q
```

Regression tests cover duplicate ambiguity, separate occurrences/categories,
stable IDs, preserved raw results, NLP support and negation, assessment conflicts,
missing-layer coverage and reconciliation failure. Live results are saved under
`evaluation/results/stage3/`. Live execution is not proof of complete detection;
existing model and dataset-label limitations still apply.

The standalone layer runners are unchanged. Document
uploads/extraction, real vision processing and the final web UI remain later stages.
