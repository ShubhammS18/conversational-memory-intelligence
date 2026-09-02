from pathlib import Path

from conversational_memory.application import RequestContext, RetrievalRequest
from conversational_memory.infrastructure import FaissVectorIndex, SQLiteMemoryRepository
from tests.integration.test_memory_service_sqlite_faiss import (
    DIMENSION,
    MODEL,
    DeterministicEmbedder,
    DeterministicMemoryIds,
    _compose,
    _request,
)


def test_restart_preserves_mapping_and_authorized_retrieval(tmp_path: Path) -> None:
    database_path = tmp_path / "memory.sqlite3"
    index_directory = tmp_path / "index"
    embedder = DeterministicEmbedder()
    first_repository = SQLiteMemoryRepository(database_path)
    first_index = FaissVectorIndex(
        index_directory,
        embedding_model=MODEL,
        vector_dimension=DIMENSION,
        create_if_missing=True,
    )
    admitted = _compose(
        first_repository,
        first_index,
        embedder,
        DeterministicMemoryIds(),
    ).admit(
        RequestContext(user_id="user-1", request_id="admit-before-restart"),
        _request(),
    )
    before_restart = first_repository.find(user_id="user-1", idempotency_key="turn-1")

    restarted_repository = SQLiteMemoryRepository(database_path)
    restarted_index = FaissVectorIndex(
        index_directory,
        embedding_model=MODEL,
        vector_dimension=DIMENSION,
    )
    retrieved = _compose(
        restarted_repository,
        restarted_index,
        embedder,
        DeterministicMemoryIds(),
    ).retrieve(
        RequestContext(user_id="user-1", request_id="retrieve-after-restart"),
        RetrievalRequest(query="Which vector database do I prefer?", limit=5, token_budget=128),
    )
    after_restart = restarted_repository.find(user_id="user-1", idempotency_key="turn-1")

    assert before_restart is not None and before_restart.indexing_work is not None
    assert after_restart is not None and after_restart.indexing_work is not None
    assert after_restart.result == admitted
    assert after_restart.indexing_work.vector_id == before_restart.indexing_work.vector_id
    assert retrieved.included_memory_ids == (admitted.memory_id,)
