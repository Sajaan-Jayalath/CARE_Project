"""Stage 1: inspect local spaCy components without bias or CARE classification."""

import json
import sys
from pathlib import Path

import pandas as pd
import spacy


PROJECT_DIR = Path(__file__).resolve().parent
DATASET_PATH = PROJECT_DIR / "datasets" / "CARE_NLP_Component_Test_Dataset.xlsx"
RESULTS_PATH = PROJECT_DIR / "results" / "nlp_component_results.xlsx"
REQUIRED_COLUMNS = ["ID", "Test Text", "NLP Component", "Expected Check"]


def analyse_text(doc):
    """Collect linguistic output only; expected checks are reviewed manually."""
    return {
        "Sentence segmentation": [sentence.text for sentence in doc.sents],
        "Tokenisation": [token.text for token in doc],
        "Token analysis (lemmatisation, POS tagging, dependency parsing)": [
            {
                "token.text": token.text,
                "token.lemma_": token.lemma_,
                "token.pos_": token.pos_,
                "token.dep_": token.dep_,
                "token.head.text": token.head.text,
            }
            for token in doc
        ],
        "Named Entity Recognition (NER)": [
            {"entity text": entity.text, "entity label": entity.label_}
            for entity in doc.ents
        ],
    }


def main():
    # Support Unicode dataset text in Windows terminals and redirected output.
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')

    if not DATASET_PATH.is_file():
        raise SystemExit(f"Dataset not found: {DATASET_PATH}")

    dataset = pd.read_excel(DATASET_PATH, engine="openpyxl", keep_default_na=False)
    missing_columns = [name for name in REQUIRED_COLUMNS if name not in dataset.columns]
    if missing_columns:
        raise SystemExit(f"Dataset is missing columns: {', '.join(missing_columns)}")
    if dataset.empty:
        raise SystemExit("The dataset contains no test cases.")

    # Validate before processing to avoid silently analysing empty or numeric cells.
    for excel_row, value in enumerate(dataset["Test Text"], start=2):
        if not isinstance(value, str) or not value.strip():
            raise SystemExit(f"Excel row {excel_row}: 'Test Text' must be non-empty text.")

    try:
        # Load only the installed pipeline; no downloads or external services.
        nlp = spacy.load("en_core_web_sm")
    except OSError as error:
        raise SystemExit("The local spaCy pipeline 'en_core_web_sm' could not be loaded.") from error

    results = []
    print("Stage 1: Individual NLP Component Testing")
    print("Expected checks are for manual review; no bias or CARE decisions are made.")
    print("An empty NER list means spaCy identified no named entities.")

    for _, row in dataset.iterrows():
        doc = nlp(row["Test Text"])
        # Show all six capabilities for each case, alongside its intended component.
        output = json.dumps(analyse_text(doc), ensure_ascii=False, indent=2)
        result = {
            "Test ID": row["ID"],
            "Test Text": row["Test Text"],
            "NLP Component": row["NLP Component"],
            "Expected Check": row["Expected Check"],
            "spaCy Output": output,
        }
        results.append(result)

        print("\n" + "=" * 72)
        for label, value in result.items():
            if label == "spaCy Output":
                print(f"{label}:\n{value}")
            else:
                print(f"{label}: {value}")

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Re-running the script replaces the previous summary workbook.
    pd.DataFrame(results).to_excel(RESULTS_PATH, index=False, engine="openpyxl")
    print(f"\nProcessed {len(results)} test cases. Results saved to: {RESULTS_PATH}")


if __name__ == "__main__":
    main()

