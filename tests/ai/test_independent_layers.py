"""Counterexamples and evidence contracts for the independent rule/NLP layers."""
import importlib.util
from pathlib import Path

import pytest
import spacy

from backend.ai.rules.rule_engine import analyze_gendered_language


@pytest.mark.parametrize("text,quotes", [
    ("The chairman asked the salesman about manpower.", ["chairman", "salesman", "manpower"]),
    ("Chairmen consulted the chairwomen.", ["Chairmen", "chairwomen"]),
    ("Every chairman must send his report.", ["chairman", "his"]),
    ("Each nurse should update\nher record before she leaves.", ["her", "she"]),
    ("Thanks   guys, please help.", ["Thanks   guys"]),
    ("Hey, guys, please help.", ["Hey, guys"]),
    ('"Every engineer must check his tools." is outdated wording.', []),
    ('The lesson rejects "mankind"; we still need manpower.', ["manpower"]),
    ('The lesson rejects "mankind". Ask the chairman.', ["chairman"]),
    ("Every student should listen to Maria when she explains the task.", []),
    ("A programmer named John submitted his code.", []),
    ("Each architect should review their design.", []),
    ("The historical document uses the title chairman.", []),
    ('Replace "you guys" with "everyone".', []),
    ("Outdated tools require more manpower.", ["manpower"]),
    ("The chairman reviewed the chairman's notes.", ["chairman", "chairman"]),
])
def test_rule_findings_and_exact_offsets(text, quotes):
    findings = analyze_gendered_language(text)
    assert [r["original_content"] for r in findings] == quotes
    assert all(text[r["start_char"]:r["end_char"]] == r["original_content"] for r in findings)


@pytest.fixture(scope="module")
def extractor():
    path = Path(__file__).parent / "nlp_test" / "nlp_relationship_analysis.py"
    spec = importlib.util.spec_from_file_location("care_relationship_tests", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.extract_relationships, spacy.load("en_core_web_sm")


@pytest.mark.parametrize("text,required,forbidden", [
    ("Female employees were appointed as directors.", {"P1"}, set()),
    ("The committee appointed women as engineers.", {"P1"}, set()),
    ("Women appointed students as engineers.", set(), {"P1"}),
    ("Men tend to be careful with unfamiliar equipment.", {"P3"}, set()),
    ("Women told men to be patient.", set(), {"P3"}),
    ("Among the skilled technicians were women.", {"P1", "P2"}, set()),
    ("Men attended a seminar about architects.", set(), {"P1"}),
    ("Women perform well at debugging.", {"P2"}, set()),
    ("The class performs well at debugging.", set(), {"P2"}),
    ("Women are not incompetent.", {"P2", "P5"}, set()),
    ("Male and female students jointly completed the task.", {"P6"}, set()),
    ("The female analyst, who was described as cautious, worked.", {"P3"}, set()),
    ("The female analyst, whose manager was cautious, worked.", set(), {"P3"}),
])
def test_nlp_relationships_not_bias_verdicts(extractor, text, required, forbidden):
    extract, nlp = extractor
    findings = extract(nlp(text))
    types = {r["type"] for r in findings}
    assert required <= types
    assert not forbidden & types
    for finding in findings:
        assert text[finding["start_char"]:finding["end_char"]] == finding["sentence"]
        assert "bias" not in finding  # Relationships must not become bias verdicts.
