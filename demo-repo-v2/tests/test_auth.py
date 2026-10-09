from app.auth import authenticate, login


def test_authenticate_lowercase_ok():
    assert authenticate("alice", "wonderland") is True


def test_login_mixed_case_should_succeed():
    """Mixed-case usernames must authenticate successfully."""
    result = login("Alice", "wonderland")
    assert result["ok"] is True
