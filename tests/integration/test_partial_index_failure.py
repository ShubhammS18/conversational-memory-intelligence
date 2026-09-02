from pathlib import Path

from tests.integration.test_memory_service_sqlite_faiss import (
    _scenario_confirmed_faiss_failure_is_failed_and_nonretrievable_until_retry,
    _scenario_faiss_success_with_failed_sqlite_ack_retries_same_id_without_duplicate,
)


def test_confirmed_faiss_failure_is_failed_and_nonretrievable_until_retry(
    tmp_path: Path,
) -> None:
    _scenario_confirmed_faiss_failure_is_failed_and_nonretrievable_until_retry(tmp_path)


def test_faiss_success_with_failed_sqlite_ack_retries_same_id_without_duplicate(
    tmp_path: Path,
) -> None:
    _scenario_faiss_success_with_failed_sqlite_ack_retries_same_id_without_duplicate(tmp_path)
