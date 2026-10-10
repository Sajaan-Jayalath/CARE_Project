from types import SimpleNamespace

from backend.ai.llm.client import LLMError
from evaluation.evaluation import Case, evaluate, main
from evaluation.import_llm_datasets import CHALLENGE_FILE, DATASET_DIR, MAIN_FILE, convert
from evaluation.metrics import calculate_metrics


def test_main_import_preserves_multi_labels_and_visual_descriptions():
    data = convert(DATASET_DIR / "source" / MAIN_FILE)
    assert len(data["cases"]) == 36
    cases = {c["id"]: c for c in data["cases"]}
    assert cases["T22"]["expected_categories"] == ["Professional Roles", "Gender Stereotypes"]
    assert cases["T16"]["document"]["sections"][0]["type"] == "image_description"
    assert cases["T03"]["expected_categories"] == []
    for case in cases.values():
        Case.model_validate(case)


def test_challenge_import_only_scores_labelled_category():
    data = convert(DATASET_DIR / "source" / CHALLENGE_FILE, challenge=True)
    assert len(data["cases"]) == 20
    assert all(c["evaluated_categories"] == ["Gendered Language"] for c in data["cases"])
    assert data["cases"][14]["expected_categories"] == []


def test_metrics_multilabel_and_failure_accounting():
    rows = [
        {"status": "completed", "expected_categories": ["Gendered Language", "Gender Stereotypes"],
         "predicted_categories": ["Gendered Language"], "evaluated_categories": ["Gendered Language", "Gender Stereotypes"]},
        {"status": "completed", "expected_categories": [], "predicted_categories": ["Gendered Language"],
         "evaluated_categories": ["Gendered Language", "Gender Stereotypes"]},
        {"status": "failed"},
    ]
    metrics = calculate_metrics(rows)
    assert metrics["micro"]["tp"] == 1
    assert metrics["micro"]["fp"] == 1
    assert metrics["micro"]["fn"] == 1
    assert metrics["micro"]["f1"] == 0.5
    assert metrics["failed_cases"] == 1
    assert metrics["completion_rate"] == 2 / 3
    assert metrics["exact_category_match_accuracy"] == 0


def test_challenge_predictions_outside_scope_not_counted_as_false_positives():
    metrics = calculate_metrics([{"status": "completed", "expected_categories": [],
                                  "predicted_categories": ["Professional Roles"],
                                  "evaluated_categories": ["Gendered Language"]}])
    assert metrics["micro"]["fp"] == 0
    assert metrics["exact_category_match_accuracy"] == 1


def test_undefined_metrics_are_null():
    metrics = calculate_metrics([{"status": "failed"}])
    assert metrics["micro"]["precision"] is None
    assert metrics["exact_category_match_accuracy"] is None
    assert metrics["completion_rate"] == 0


def test_evaluator_does_not_send_labels_and_preserves_failure():
    dataset = convert(DATASET_DIR / "source" / MAIN_FILE)
    cases = [Case.model_validate(c) for c in dataset["cases"][:2]]

    def fail(document):
        assert "expected_categories" not in document.model_dump()
        raise LLMError("provider_timeout", "Timed out")

    report = evaluate(cases, SimpleNamespace(analyse_document=fail))
    assert report["metrics"]["failed_cases"] == 2
    assert all("predicted_categories" not in row for row in report["cases"])


def test_dry_run_never_constructs_provider(monkeypatch, capsys):
    from backend.ai.llm.analyser import LLMAnalyser

    def forbidden():
        raise AssertionError("A dry run must not instantiate an API client")
    monkeypatch.setattr(LLMAnalyser, "from_env", forbidden)
    assert main(["--dataset", str(DATASET_DIR / "care_main_v1.json"), "--dry-run"]) == 0
    assert '"api_calls": 0' in capsys.readouterr().out
