import pytest

from conversational_memory.domain.admission import evaluate_credential_admission
from conversational_memory.domain.idempotency import RequestFingerprintInput
from conversational_memory.domain.models import AdmissionDecision


def _candidate(content: str, *, value: object = None) -> RequestFingerprintInput:
    return RequestFingerprintInput(
        conversation_id="conversation-1",
        turn_id="turn-1",
        content=content,
        memory_type="fact",
        subject="credential policy",
        value=value,
        source_type="explicit_user",
        source_event_at=None,
        valid_from=None,
        valid_until=None,
    )


@pytest.mark.parametrize(
    "content",
    [
        "-----BEGIN PRIVATE KEY-----",
        "-----BEGIN RSA PRIVATE KEY-----",
        "-----BEGIN EC PRIVATE KEY-----",
        "-----BEGIN OPENSSH PRIVATE KEY-----",
        "-----BEGIN PGP PRIVATE KEY BLOCK-----",
        "password=hunter2",
        "passcode: 482913",
        "My API key is sk-example-secret",
        "access_token=token-value",
        "refresh token: refresh-value",
        "secret-key = secret-value",
        "Use sk-example-secret for this request",
    ],
)
def test_credential_policy_rejects_minimum_positive_cases(content: str) -> None:
    result = evaluate_credential_admission(_candidate(content))

    assert result.decision is AdmissionDecision.REJECTED
    assert result.reason == "sensitive_credential"


@pytest.mark.parametrize(
    "content",
    [
        "-----BEGIN PUBLIC KEY-----",
        "Discuss private key rotation",
        "I use a password manager",
        "Rotate the API key",
        "Access token budgeting is important",
        "Review the secret key rotation policy",
        "The prefix is sk-",
        "The example is sk-short",
    ],
)
def test_credential_policy_preserves_required_near_misses(content: str) -> None:
    result = evaluate_credential_admission(_candidate(content))

    assert result.decision is AdmissionDecision.ACCEPTED
    assert result.reason == "credential_policy_passed"


def test_credential_policy_scans_nested_value_keys_and_values() -> None:
    result = evaluate_credential_admission(
        _candidate("Store a configuration detail", value={"nested": ["password=hunter2"]})
    )

    assert result.decision is AdmissionDecision.REJECTED


def test_credential_policy_fails_closed_for_unscannable_input() -> None:
    result = evaluate_credential_admission(
        _candidate("Store a configuration detail", value=object())
    )

    assert result.decision is AdmissionDecision.REJECTED
    assert result.reason == "sensitive_check_unavailable"


def test_credential_policy_fails_closed_for_unexpected_scan_error() -> None:
    class ExplodingSequence(list[object]):
        def __iter__(self):
            raise LookupError("scan failed")

    result = evaluate_credential_admission(
        _candidate("Store a configuration detail", value=ExplodingSequence())
    )

    assert result.decision is AdmissionDecision.REJECTED
    assert result.reason == "sensitive_check_unavailable"
