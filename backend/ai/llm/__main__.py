"""Run with python -m backend.ai.llm --text '...' or --input document.json."""
import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from backend.ai.llm.analyser import LLMAnalyser
from backend.ai.llm.client import LLMError
from backend.ai.schemas.input_schema import Document


def main(argv=None):
    parser = argparse.ArgumentParser(description="Independent CARE LLM analysis (live API call).")
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--text", help="Text to analyse")
    inputs.add_argument("--input", type=Path, help="Structured document JSON")
    parser.add_argument("--output", type=Path, help="Save validated result JSON")
    args = parser.parse_args(argv)
    try:
        document = (Document.from_text(args.text) if args.text is not None else
                    Document.model_validate_json(args.input.read_text(encoding="utf-8-sig")))
        result = LLMAnalyser.from_env().analyse_document(document)
        content = result.model_dump_json(indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(content + "\n", encoding="utf-8")
        else:
            print(content)
        return 0
    except LLMError as exc:
        print(json.dumps({"status": "failed", "error": {"code": exc.code, "message": str(exc)}}), file=sys.stderr)
    except (OSError, ValidationError, ValueError):
        print(json.dumps({"status": "failed", "error": {"code": "invalid_input", "message": "Could not read/write files or validate the input; check paths and document schema."}}), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
