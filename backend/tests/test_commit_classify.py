"""Unit tests for P2 commit-search classification (no Docker)."""

from types import SimpleNamespace

from app.models.entities import StepClassification
from app.services.commit_search import _classify_probe, _matches_target_failure


def _bundle(**overrides):
    base = {
        "test_node_id": "tests/test_auth.py::test_login_mixed_case_should_succeed",
        "failure_exception_type": "AssertionError",
        "failure_message_fragment": "mixed-case",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_classify_passed_is_good():
    cls, reason = _classify_probe(
        _bundle(),
        exit_code=0,
        results=[
            {
                "test_identifier": "tests/test_auth.py::test_login_mixed_case_should_succeed",
                "outcome": "passed",
                "failure_message": "",
                "traceback": "",
            }
        ],
        stdout="1 passed",
        stderr="",
        timed_out=False,
        cancelled=False,
        error_category=None,
    )
    assert cls == StepClassification.good
    assert "passed" in reason


def test_classify_target_failure_is_bad():
    cls, reason = _classify_probe(
        _bundle(),
        exit_code=1,
        results=[
            {
                "test_identifier": "tests/test_auth.py::test_login_mixed_case_should_succeed",
                "outcome": "failed",
                "failure_message": "AssertionError: mixed-case login should succeed",
                "traceback": "",
            }
        ],
        stdout="AssertionError: mixed-case login should succeed",
        stderr="",
        timed_out=False,
        cancelled=False,
        error_category=None,
    )
    assert cls == StepClassification.bad
    assert "matched" in reason.lower() or "target" in reason.lower()


def test_classify_unrelated_failure_is_skip():
    cls, reason = _classify_probe(
        _bundle(),
        exit_code=1,
        results=[
            {
                "test_identifier": "tests/test_auth.py::test_login_mixed_case_should_succeed",
                "outcome": "failed",
                "failure_message": "AssertionError: unrelated timeout helper",
                "traceback": "",
            }
        ],
        stdout="AssertionError: unrelated timeout helper",
        stderr="",
        timed_out=False,
        cancelled=False,
        error_category=None,
    )
    assert cls == StepClassification.skip
    assert "unrelated" in reason


def test_classify_collection_error_is_skip():
    cls, reason = _classify_probe(
        _bundle(),
        exit_code=2,
        results=[],
        stdout="ERROR: error during collection",
        stderr="",
        timed_out=False,
        cancelled=False,
        error_category="collection_error",
    )
    assert cls == StepClassification.skip


def test_classify_missing_deps_is_skip():
    cls, reason = _classify_probe(
        _bundle(),
        exit_code=-1,
        results=[],
        stdout="",
        stderr="ModuleNotFoundError",
        timed_out=False,
        cancelled=False,
        error_category="environment_setup_unsupported",
    )
    assert cls == StepClassification.skip
    assert "dependencies" in reason or "environment" in reason


def test_classify_cancel_is_abort():
    cls, reason = _classify_probe(
        _bundle(),
        exit_code=-1,
        results=[],
        stdout="",
        stderr="",
        timed_out=False,
        cancelled=True,
        error_category=None,
    )
    assert cls == StepClassification.abort
    assert reason == "cancelled"


def test_matches_target_failure_requires_fragment():
    ok, _ = _matches_target_failure(
        _bundle(failure_message_fragment="mixed-case"),
        [
            {
                "test_identifier": "tests/test_auth.py::test_login_mixed_case_should_succeed",
                "outcome": "failed",
                "failure_message": "AssertionError: wrong password",
                "traceback": "",
            }
        ],
        "AssertionError: wrong password",
        "",
    )
    assert ok is False


def test_matches_pytest_assertion_rewrite_without_literal_exception():
    ok, reason = _matches_target_failure(
        _bundle(failure_message_fragment="assert False is True"),
        [
            {
                "test_identifier": "tests/test_auth.py::test_login_mixed_case_should_succeed",
                "outcome": "failed",
                "failure_message": "assert False is True",
                "traceback": "E   assert False is True",
            }
        ],
        "",
        "",
    )
    assert ok is True
    assert "matched" in reason
