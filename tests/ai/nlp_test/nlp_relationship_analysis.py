"""Stage 2: local CARE relationship extraction and development-set evaluation.

Run: python nlp_relationship_analysis.py
Requires locally installed spacy, en_core_web_sm, pandas and openpyxl.

This is a vocabulary-assisted dependency extractor, not a bias classifier.
Expected labels and CARE metadata are used only by the evaluation layer.
Limitations: finite concept coverage, parser errors, no name-based gender
inference, no general coreference, and no document-level interpretation.
Negated/quoted relationships remain linguistic relationships, not assertions.
"""

import json
from pathlib import Path
import re
import sys

import pandas as pd
import spacy


PROJECT_DIR = Path(__file__).resolve().parent
DATASET_PATH = PROJECT_DIR / "datasets" / "CARE_NLP_Relationship_Development_Dataset.xlsx"
RESULTS_PATH = PROJECT_DIR.parents[2] / "evaluation" / "results" / "nlp" / "nlp_relationship_development_results.xlsx"
REQUIRED_COLUMNS = [
    "ID", "Test Text", "NLP Relationship Type", "CARE Category",
    "CARE Scenario ID", "Expected Relationship", "Reason",
]
# Evaluation imports the production extractor; there is only one implementation.
PROJECT_ROOT = PROJECT_DIR.parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from backend.ai.preprocessing.entity_analyser import (
    EXTRACTOR_VERSION, RELATIONSHIP_TYPES, extract_relationships,
)


def load_dataset(path):
    dataset = pd.read_excel(path, engine="openpyxl", keep_default_na=False)
    missing = set(REQUIRED_COLUMNS) - set(dataset.columns)
    if missing:
        raise ValueError(f"Missing dataset columns: {sorted(missing)}")
    if dataset.empty:
        raise ValueError("The development dataset is empty.")
    for index, row in dataset.iterrows():
        if not isinstance(row["Test Text"], str) or not row["Test Text"].strip():
            raise ValueError(f"Excel row {index + 2}: Test Text must be non-empty text.")
        if str(row["Expected Relationship"]).strip().lower() not in {"yes", "no"}:
            raise ValueError(f"Excel row {index + 2}: Expected Relationship must be Yes or No.")
        target_code(row["NLP Relationship Type"])
    return dataset


def target_code(value):
    match = re.match(r"^(?:NLP-)?(P[1-6])\b", str(value).strip())
    if not match:
        raise ValueError(f"Unsupported NLP Relationship Type: {value!r}")
    return match.group(1)


def evaluate_dataset(dataset, nlp):
    """Keep evaluation labels separate from the reusable document extractor."""
    rows = []
    for _, row in dataset.iterrows():
        doc = nlp(row["Test Text"])
        all_matches = extract_relationships(doc)
        target = target_code(row["NLP Relationship Type"])
        matches = [item for item in all_matches if item["type"] == target]
        expected = str(row["Expected Relationship"]).strip().title()
        detected = "Yes" if matches else "No"

        def joined(field):
            return " | ".join(dict.fromkeys(str(item[field]) for item in matches))

        rows.append({
            "Test ID": row["ID"],
            "Test Text": row["Test Text"],
            "Target NLP Relationship Type": row["NLP Relationship Type"],
            "CARE Category": row["CARE Category"],
            "CARE Scenario ID": row["CARE Scenario ID"],
            "Expected Relationship": expected,
            "Detected Relationship": detected,
            "Matched Gender Term": joined("gender_term"),
            "Matched Related Term": joined("related_term"),
            "Relationship / Predicate": joined("predicate"),
            "Linguistic Evidence": joined("evidence"),
            "Pass/Fail": "Pass" if detected == expected else "Fail",
            "Interpretation": (
                "Potential CARE-relevant linguistic relationship detected" if matches
                else "No supported target relationship extracted; this is not a bias verdict"
            ),
            "Reason": row["Reason"],
            "All Detected Types": ", ".join(sorted({item["type"] for item in all_matches})),
            "Unscored Detected Types": ", ".join(sorted({item["type"] for item in all_matches} - {target})),
            "Scoring Scope": "Only the supplied target relationship is labelled; other types are unscored.",
            "Target Evidence JSON": json.dumps(matches, ensure_ascii=False),
            "Dependency Diagnostics": " | ".join(
                f"{t.text}[{t.i}] lemma={t.lemma_} POS={t.pos_} "
                f"-{t.dep_}-> {t.head.text}[{t.head.i}]" for t in doc
            ),
            "spaCy Version": spacy.__version__,
            "Model Version": nlp.meta.get("version", ""),
            "Extractor Version": EXTRACTOR_VERSION,
        })
    return pd.DataFrame(rows)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        dataset = load_dataset(DATASET_PATH)
        nlp = spacy.load("en_core_web_sm")  # Local model only; never download.
        results = evaluate_dataset(dataset, nlp)
        RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        results.to_excel(RESULTS_PATH, index=False, engine="openpyxl")
    except (OSError, ValueError, ImportError) as error:
        raise SystemExit(f"Stage 2 could not complete: {error}") from error

    passed = results["Pass/Fail"].eq("Pass")
    expected_yes = results["Expected Relationship"].eq("Yes")
    print("Stage 2 development checks — not overall CARE accuracy")
    print(f"Total test cases: {len(results)}")
    print(f"Passed: {int(passed.sum())}")
    print(f"Failed: {int((~passed).sum())}")
    print(f"Expected Yes correctly detected: {int((passed & expected_yes).sum())}")
    print(f"Expected No correctly rejected: {int((passed & ~expected_yes).sum())}")
    failed_ids = results.loc[~passed, "Test ID"].astype(str).tolist()
    print("Failed test IDs: " + (", ".join(failed_ids) if failed_ids else "None"))
    print(f"Results saved to: {RESULTS_PATH}")
    print("Local linguistic evidence only; no bias verdict or representation-imbalance claim.")


if __name__ == "__main__":
    main()

