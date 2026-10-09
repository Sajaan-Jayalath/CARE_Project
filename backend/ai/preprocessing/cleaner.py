"""Keep wording, punctuation and spacing intact for reliable source highlights."""
from backend.ai.schemas.input_schema import Document


def prepare_text(document):
    # No lowercasing, stop-word removal or whitespace rewriting: these change
    # meaning and invalidate source offsets. Extraction already omits empty units.
    return Document.model_validate(document).model_copy(deep=True)
