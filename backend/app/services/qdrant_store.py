from __future__ import annotations

import uuid
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from app.core.config import get_settings


def get_client() -> QdrantClient:
    settings = get_settings()
    return QdrantClient(url=settings.qdrant_url, timeout=60)


def ensure_collection() -> None:
    settings = get_settings()
    client = get_client()
    names = {c.name for c in client.get_collections().collections}
    if settings.qdrant_collection in names:
        return
    client.create_collection(
        collection_name=settings.qdrant_collection,
        vectors_config=qm.VectorParams(
            size=settings.embedding_dimension,
            distance=qm.Distance.COSINE,
        ),
    )
    client.create_payload_index(
        collection_name=settings.qdrant_collection,
        field_name="repository_id",
        field_schema=qm.PayloadSchemaType.KEYWORD,
    )
    client.create_payload_index(
        collection_name=settings.qdrant_collection,
        field_name="index_id",
        field_schema=qm.PayloadSchemaType.KEYWORD,
    )


def upsert_chunks(
    *,
    repository_id: uuid.UUID,
    index_id: uuid.UUID,
    points: list[dict[str, Any]],
) -> None:
    if not points:
        return
    ensure_collection()
    settings = get_settings()
    client = get_client()
    client.upsert(
        collection_name=settings.qdrant_collection,
        points=[
            qm.PointStruct(
                id=p["id"],
                vector=p["vector"],
                payload={
                    "repository_id": str(repository_id),
                    "index_id": str(index_id),
                    "chunk_id": p["chunk_id"],
                    "file_path": p["file_path"],
                },
            )
            for p in points
        ],
    )


def search(
    *,
    repository_id: uuid.UUID,
    index_id: uuid.UUID,
    vector: list[float],
    limit: int = 30,
) -> list[dict[str, Any]]:
    ensure_collection()
    settings = get_settings()
    client = get_client()
    results = client.search(
        collection_name=settings.qdrant_collection,
        query_vector=vector,
        query_filter=qm.Filter(
            must=[
                qm.FieldCondition(
                    key="repository_id",
                    match=qm.MatchValue(value=str(repository_id)),
                ),
                qm.FieldCondition(
                    key="index_id",
                    match=qm.MatchValue(value=str(index_id)),
                ),
            ]
        ),
        limit=limit,
        with_payload=True,
    )
    out: list[dict[str, Any]] = []
    for r in results:
        payload = r.payload or {}
        out.append(
            {
                "chunk_id": payload.get("chunk_id"),
                "file_path": payload.get("file_path"),
                "score": float(r.score),
            }
        )
    return out


def delete_index_points(index_id: uuid.UUID) -> None:
    settings = get_settings()
    client = get_client()
    names = {c.name for c in client.get_collections().collections}
    if settings.qdrant_collection not in names:
        return
    client.delete(
        collection_name=settings.qdrant_collection,
        points_selector=qm.FilterSelector(
            filter=qm.Filter(
                must=[
                    qm.FieldCondition(
                        key="index_id",
                        match=qm.MatchValue(value=str(index_id)),
                    )
                ]
            )
        ),
    )


def delete_repository_points(repository_id: uuid.UUID) -> None:
    settings = get_settings()
    client = get_client()
    names = {c.name for c in client.get_collections().collections}
    if settings.qdrant_collection not in names:
        return
    client.delete(
        collection_name=settings.qdrant_collection,
        points_selector=qm.FilterSelector(
            filter=qm.Filter(
                must=[
                    qm.FieldCondition(
                        key="repository_id",
                        match=qm.MatchValue(value=str(repository_id)),
                    )
                ]
            )
        ),
    )


def healthcheck() -> tuple[bool, str]:
    try:
        client = get_client()
        client.get_collections()
        return True, "ok"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
