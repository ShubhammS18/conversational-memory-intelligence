from pathlib import Path

from tests.integration.test_memory_service_sqlite_faiss import (
    _scenario_admission_persists_pending_before_same_stable_id_is_indexed,
    _scenario_credential_rejection_creates_no_sqlite_or_faiss_mutation,
)


def test_admission_persists_pending_before_same_stable_id_is_indexed(
    tmp_path: Path,
) -> None:
    _scenario_admission_persists_pending_before_same_stable_id_is_indexed(tmp_path)


def test_credential_rejection_creates_no_sqlite_or_faiss_mutation(tmp_path: Path) -> None:
    _scenario_credential_rejection_creates_no_sqlite_or_faiss_mutation(tmp_path)
