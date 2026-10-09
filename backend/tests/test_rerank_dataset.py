import json
from pathlib import Path

from app.services.rerank_dataset import validate_dataset
from app.services.rerank_eval import aggregate_chunk_to_file, file_metrics


def _ex(**over):
    base = {
        "example_id": "e1",
        "bug_group_id": "g1",
        "repository_id": "r1",
        "commit_sha": "abc",
        "query": "login fails for Alice",
        "file_path": "app/auth.py",
        "snippet": "return username",
        "label": "relevant",
        "label_source": "human_review",
        "source_reference": "demo",
        "split": "test",
    }
    base.update(over)
    return base


def test_validation_catches_missing_provenance():
    rows = [_ex(source_reference="")]
    r = validate_dataset(rows)
    assert not r.ok
    assert any(i.code == "missing_provenance" for i in r.issues)


def test_validation_invalid_label():
    rows = [_ex(label="maybe")]
    r = validate_dataset(rows)
    assert not r.ok
    assert any(i.code == "invalid_label" for i in r.issues)


def test_split_leakage_same_bug_group():
    rows = [
        _ex(example_id="a", split="train"),
        _ex(example_id="b", bug_group_id="g1", split="test", file_path="app/main.py", snippet="main"),
    ]
    r = validate_dataset(rows)
    assert not r.ok
    assert any(i.code == "split_leakage" for i in r.issues)


def test_unreviewed_blocked_from_train():
    rows = [_ex(label="unreviewed", split="train", example_id="u1")]
    r = validate_dataset(rows)
    assert not r.ok
    assert any(i.code == "unreviewed_in_train" for i in r.issues)


def test_smoke_dataset_valid_and_within_repo(tmp_path: Path):
    src = Path(__file__).resolve().parents[1] / "data" / "rerank" / "datasets" / "login_smoke_v1.jsonl"
    assert src.exists()
    rows = [json.loads(l) for l in src.read_text().splitlines() if l.strip()]
    # drop unreviewed for strict ok if near-dup with auth pos — unreviewed has different snippet slice
    r = validate_dataset(rows)
    assert r.ok, [i.__dict__ for i in r.issues]
    assert r.bug_group_count == 1
    assert r.split_strategy == "within_repository"
    assert r.repository_count == 1


def test_file_metrics_and_aggregation():
    ranked = aggregate_chunk_to_file(
        [
            {"file_path": "a.py"},
            {"file_path": "a.py"},
            {"file_path": "b.py"},
        ]
    )
    assert ranked == ["a.py", "b.py"]
    m = file_metrics(["b.py", "a.py"], {"a.py"}, {"a.py", "b.py"})
    assert m["hit_at_1"] == 0.0
    assert m["hit_at_5"] == 1.0
    assert m["mrr_at_10"] == 0.5
    assert m["candidate_recall"] == 1.0
