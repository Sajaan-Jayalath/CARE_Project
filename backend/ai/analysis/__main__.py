"""Run the integrated CARE engine: python -m backend.ai.analysis --text "Your teaching text"."""
import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from backend.ai.analysis.care_engine import CAREEngine
from backend.ai.schemas.input_schema import Document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--text")
    inputs.add_argument("--input", type=Path, help="Shared Document JSON file, not a PDF/Office upload")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        doc = Document.from_text(args.text) if args.text is not None else Document.model_validate_json(args.input.read_text(encoding="utf-8-sig"))
        result = CAREEngine().analyse_document(doc)
        payload = result.model_dump_json(indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(payload + "\n", encoding="utf-8")
        print(payload)
        return 0 if result.status == "completed" else 1
    except (ValidationError, OSError, ValueError):
        print(json.dumps({"status": "failed", "error": "Invalid document or inaccessible input/output path."}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
