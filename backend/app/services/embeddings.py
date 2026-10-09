from __future__ import annotations

import logging
from functools import lru_cache

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache
def get_embedding_model():
    settings = get_settings()
    from sentence_transformers import SentenceTransformer

    logger.info("Loading embedding model %s", settings.embedding_model)
    return SentenceTransformer(settings.embedding_model)


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    model = get_embedding_model()
    vectors = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return [v.tolist() for v in vectors]


def embed_query(text: str) -> list[float]:
    return embed_texts([text])[0]
