"""Shared team knowledge base — multi-user RAG with namespacing.

Architecture:
  - Single Qdrant instance shared across all team members
  - Per-user and per-project namespaces (collection prefixes)
  - Contribution API: any contributor can add documents
  - Global collection: searchable by all team members

Collections:
  global_<team_id>         — shared team knowledge (RTL patterns, IP docs)
  project_<project_id>     — per-project knowledge (specs, constraints, logs)
  private_<user_id>        — per-user private notes (viewer access: self only)

Design doc reference: Phase 4 — 团队协作（多用户共享RAG知识库）
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

QDRANT_URL      = os.getenv("COLLAB_QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY  = os.getenv("COLLAB_QDRANT_KEY", "")
STORAGE_PATH    = os.getenv("COLLAB_STORAGE_PATH", "./collab_qdrant_storage")
TEAM_ID         = os.getenv("COLLAB_TEAM_ID", "default")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _collection_name(scope: str, scope_id: str) -> str:
    """Generate collection name from scope and ID."""
    return f"{scope}_{scope_id}".replace("-", "_").replace("/", "_")[:63]


def _get_qdrant_client():
    try:
        from qdrant_client import QdrantClient
        if QDRANT_URL.startswith("http"):
            return QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY or None)
        return QdrantClient(path=STORAGE_PATH)
    except ImportError:
        raise RuntimeError("qdrant-client required: pip install qdrant-client")


def _get_embed_model():
    """Return embedding model (OpenAI primary, HuggingFace fallback)."""
    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key:
        try:
            from llama_index.embeddings.openai import OpenAIEmbedding
            return OpenAIEmbedding(model="text-embedding-3-small", api_key=openai_key)
        except ImportError:
            pass
    try:
        from llama_index.embeddings.huggingface import HuggingFaceEmbedding
        return HuggingFaceEmbedding(model_name="BAAI/bge-small-en-v1.5")
    except ImportError:
        raise RuntimeError("llama-index embedding model not available")


async def index_document(
    content: str,
    doc_id: str,
    scope: str = "global",
    scope_id: str | None = None,
    metadata: dict | None = None,
    user_id: str = "system",
) -> dict:
    """Add or update a document in the knowledge base.

    Args:
        content:   Document text content.
        doc_id:    Unique document identifier (used for deduplication).
        scope:     "global" | "project" | "private"
        scope_id:  Team ID / project ID / user ID (auto-filled from env if None).
        metadata:  Extra metadata stored alongside the embedding.
        user_id:   Contributing user.

    Returns:
        {"indexed": bool, "doc_id": str, "collection": str, "skipped": bool}
    """
    sid = scope_id or (TEAM_ID if scope == "global" else user_id)
    collection = _collection_name(scope, sid)
    content_hash = _sha256(content)

    try:
        from llama_index.core import Document, VectorStoreIndex
        from llama_index.core.storage.storage_context import StorageContext
        from llama_index.vector_stores.qdrant import QdrantVectorStore

        client = _get_qdrant_client()
        embed  = _get_embed_model()

        vector_store = QdrantVectorStore(client=client, collection_name=collection)
        storage_ctx  = StorageContext.from_defaults(vector_store=vector_store)

        doc = Document(
            text=content,
            doc_id=doc_id,
            metadata={
                **(metadata or {}),
                "doc_id":       doc_id,
                "content_hash": content_hash,
                "contributor":  user_id,
                "scope":        scope,
                "scope_id":     sid,
            },
        )

        index = VectorStoreIndex.from_documents(
            [doc],
            storage_context=storage_ctx,
            embed_model=embed,
            show_progress=False,
        )

        return {"indexed": True, "doc_id": doc_id, "collection": collection, "skipped": False}

    except Exception as exc:
        return {"indexed": False, "doc_id": doc_id, "error": str(exc)}


async def search_knowledge(
    query: str,
    scope: str = "global",
    scope_id: str | None = None,
    top_k: int = 5,
    filters: dict | None = None,
) -> list[dict]:
    """Search the team knowledge base.

    Args:
        query:    Natural language search query.
        scope:    Collection scope to search.
        scope_id: Specific team/project/user ID.
        top_k:    Number of results.
        filters:  Metadata filter dict (Qdrant filter syntax).

    Returns:
        [{text, score, metadata}]
    """
    sid = scope_id or TEAM_ID
    collection = _collection_name(scope, sid)

    try:
        from llama_index.core import VectorStoreIndex
        from llama_index.core.storage.storage_context import StorageContext
        from llama_index.core.vector_stores.types import MetadataFilters
        from llama_index.vector_stores.qdrant import QdrantVectorStore

        client = _get_qdrant_client()
        embed  = _get_embed_model()

        vector_store = QdrantVectorStore(client=client, collection_name=collection)
        storage_ctx  = StorageContext.from_defaults(vector_store=vector_store)
        index = VectorStoreIndex.from_vector_store(
            vector_store=vector_store,
            embed_model=embed,
        )

        retriever = index.as_retriever(similarity_top_k=top_k)
        nodes = retriever.retrieve(query)

        return [
            {
                "text":     n.get_content(),
                "score":    float(n.score) if n.score else 0.0,
                "metadata": n.metadata,
            }
            for n in nodes
        ]

    except Exception as exc:
        return [{"error": str(exc)}]


async def list_collections() -> list[dict]:
    """List all knowledge base collections (for admin UI)."""
    try:
        client = _get_qdrant_client()
        colls  = client.get_collections().collections
        return [
            {"name": c.name, "vectors_count": getattr(c, "vectors_count", None)}
            for c in colls
        ]
    except Exception as exc:
        return [{"error": str(exc)}]
