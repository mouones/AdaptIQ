"""Regression tests for test cleanup test users behavior."""

from scripts.cleanup_test_users import identity_bucket_names, is_generated_test_identity


def test_generated_test_identity_matches_known_live_prefixes() -> None:
    generated = [
        ("livepvpfix-123@example.com", "livepvpfix_123"),
        ("challenge-audit@example.com", "challenge_audit"),
        ("auditpvp-fix@example.com", "auditpvp_fix"),
        ("test_skip@example.com", "test_skip_case"),
        ("test-anything@real-domain.invalid", "test_anything"),
        ("copilot-flow@example.com", "copilot_flow"),
        ("geo_scope@example.com", "geo_scope_main"),
        ("pvp-e2e@example.com", "pvp_sec_cookies"),
        ("sec_cookies@example.com", "sec_cookies_2026"),
        ("sec_a_123@example.com", "sec_a_123"),
        ("challenge_deep_123@example.com", "challenge_deep_123"),
        ("test_custom_123@example.com", "test_custom_123"),
        ("onboarding_123@example.com", "onboarding_123"),
        ("flowtest@example.com", "flowtest_user"),
        ("e2e_user@example.com", "e2e_user_1"),
        ("qa+e2e_123@example.com", "learner"),
        ("qa+test_123@example.com", "learner"),
        ("qa+copilot_123@example.com", "learner"),
    ]

    for email, username in generated:
        assert is_generated_test_identity(email, username), (email, username)


def test_generated_test_identity_preserves_normal_accounts() -> None:
    preserved = [
        ("learner@example.com", "learner"),
        ("admin.master@example.com", "admin_master"),
        ("historyfan@example.com", "historyfan"),
        ("geography_scholar@example.com", "geography_scholar"),
        ("contestwinner@example.com", "contestwinner"),
    ]

    for email, username in preserved:
        assert not is_generated_test_identity(email, username), (email, username)


def test_identity_bucket_names_are_redacted_and_grouped() -> None:
    assert identity_bucket_names("qa+test_123@example.com", "learner") == ["test_prefix"]
    assert identity_bucket_names("user@example.com", "copilot_case") == ["copilot_prefix"]
