from app.services.sandbox import _categorize_pytest_exit


def test_assertion_failure_exit():
    assert _categorize_pytest_exit(1, 2, "", "") == "assertion_failure"


def test_no_tests_exit():
    assert _categorize_pytest_exit(5, 0, "", "") == "no_tests_collected"


def test_missing_dependency_message():
    assert (
        _categorize_pytest_exit(2, 0, "", "ModuleNotFoundError: No module named 'foo'")
        == "environment_setup_unsupported"
    )
