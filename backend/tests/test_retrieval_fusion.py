import uuid

from app.services.retrieval import reciprocal_rank_fusion


def test_rrf_prefers_overlap():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    fused = reciprocal_rank_fusion([[a, b], [a, c]])
    assert fused[0][0] == a


def test_index_isolation_filter_shape():
    """Semantic search must always scope by repository_id and index_id."""
    from app.services import qdrant_store
    import inspect

    source = inspect.getsource(qdrant_store.search)
    assert "repository_id" in source
    assert "index_id" in source
