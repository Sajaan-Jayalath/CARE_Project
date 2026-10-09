"""Choose readable chunk boundaries without deleting or rewriting characters."""
import re


def boundary(text, maximum):
    if maximum >= len(text):
        return len(text)
    prefix = text[:maximum]
    # Prefer a paragraph, then a sentence, then whitespace. Only split a token
    # when it is longer than the available context budget.
    for pattern in (r"\n\s*\n", r"[.!?][\"'’”]?\s+", r"\s+"):
        candidates = [m.end() for m in re.finditer(pattern, prefix) if m.end() >= maximum // 2]
        if candidates:
            return candidates[-1]
    return maximum
