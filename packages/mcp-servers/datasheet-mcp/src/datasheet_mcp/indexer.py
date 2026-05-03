"""LlamaIndex + Qdrant incremental document indexer.

Supports: PDF, plain text, Markdown.
Incremental update: tracks file SHA-256 hash, only rebuilds changed documents.
Embedding: OpenAI ``text-embedding-3-small`` (primary) or HuggingFace
(offline fallback, installed via the ``[offline]`` extra).
Storage: Qdrant local persistent mode.

Storage location is resolved once at import time to a cross-platform absolute
path. Precedence:

    1. ``VIBE4FPGA_DATASHEET_DB_PATH`` env var override (any absolute or
       user-expanded path).
    2. ``~/.vibe4fpga/datasheet_qdrant`` (default, works on macOS / Linux /
       Windows without cwd ambiguity).

The legacy ``./qdrant_storage`` relative path has been removed because it
resolved unpredictably depending on the directory the MCP host happened to
launch the server from.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


def _resolve_qdrant_root() -> Path:
    """Return the absolute directory used for Qdrant persistence + hash cache."""
    override = os.getenv("VIBE4FPGA_DATASHEET_DB_PATH", "").strip()
    if override:
        root = Path(override).expanduser().resolve()
    else:
        root = (Path.home() / ".vibe4fpga" / "datasheet_qdrant").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


QDRANT_ROOT     = _resolve_qdrant_root()
QDRANT_PATH     = str(QDRANT_ROOT)
HASH_CACHE_PATH = QDRANT_ROOT / "file_hashes.json"
INDEX_PERSIST_DIR = str(QDRANT_ROOT / "index")
COLLECTION_NAME = "fpga_knowledge"


def _sha256(file_path: str) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_hash_cache() -> dict[str, str]:
    if HASH_CACHE_PATH.exists():
        return json.loads(HASH_CACHE_PATH.read_text())
    return {}


def _save_hash_cache(cache: dict[str, str]) -> None:
    HASH_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    HASH_CACHE_PATH.write_text(json.dumps(cache, indent=2))


def _get_embed_model():
    """Return embedding model: OpenAI if API key present, HuggingFace otherwise.

    OpenAI is kept as a direct ``openai`` SDK dependency because
    ``vibe4fpga-llm-client`` currently only models chat completions, not
    embeddings. If that library ever grows an embeddings surface, this is the
    one place to swap.
    """
    api_key = os.getenv("OPENAI_API_KEY", "")
    if api_key:
        from llama_index.embeddings.openai import OpenAIEmbedding
        return OpenAIEmbedding(model="text-embedding-3-small", api_key=api_key)

    # Offline fallback — requires the ``[offline]`` extra
    try:
        from llama_index.embeddings.huggingface import HuggingFaceEmbedding
        return HuggingFaceEmbedding(model_name="BAAI/bge-small-en-v1.5")
    except ImportError as exc:
        raise RuntimeError(
            "No embedding model available. Set OPENAI_API_KEY or install the "
            "offline extra: `uv tool install 'datasheet-mcp[offline]'`."
        ) from exc


def _get_index(embed_model=None):
    """Load or create the Qdrant vector index."""
    from llama_index.core import Settings, VectorStoreIndex
    from llama_index.vector_stores.qdrant import QdrantVectorStore
    from qdrant_client import QdrantClient

    if embed_model is None:
        embed_model = _get_embed_model()

    Settings.embed_model = embed_model
    Settings.chunk_size    = 512
    Settings.chunk_overlap = 50

    client       = QdrantClient(path=QDRANT_PATH)
    vector_store = QdrantVectorStore(client=client, collection_name=COLLECTION_NAME)

    # Try to load existing index
    try:
        from llama_index.core import StorageContext, load_index_from_storage
        storage_context = StorageContext.from_defaults(
            vector_store=vector_store,
            persist_dir=INDEX_PERSIST_DIR,
        )
        return load_index_from_storage(storage_context), vector_store, client
    except Exception:
        # Create new empty index
        from llama_index.core import StorageContext
        storage_context = StorageContext.from_defaults(vector_store=vector_store)
        index = VectorStoreIndex([], storage_context=storage_context)
        return index, vector_store, client


def index_document(file_path: str, doc_type: str = "datasheet") -> dict:
    """Index or re-index a document. Only rebuilds vectors for changed files.

    Args:
        file_path: Absolute path to PDF or text file.
        doc_type:  "datasheet" | "protocol" | "design_pattern" | "ip_interface"

    Returns:
        {"status": "indexed"|"skipped"|"error", "chunks": int, "file": str}
    """
    fp = Path(file_path)
    if not fp.exists():
        return {"status": "error", "message": f"File not found: {file_path}"}

    current_hash = _sha256(file_path)
    cache = _load_hash_cache()

    if cache.get(file_path) == current_hash:
        return {"status": "skipped", "reason": "file unchanged", "file": file_path}

    try:
        from llama_index.core import SimpleDirectoryReader
        docs = SimpleDirectoryReader(
            input_files=[file_path],
        ).load_data()

        # Add metadata for filtering
        for doc in docs:
            doc.metadata["doc_type"]  = doc_type
            doc.metadata["file_path"] = file_path
            doc.metadata["file_name"] = fp.name

        index, _, _ = _get_index()
        for doc in docs:
            index.insert(doc)

        index.storage_context.persist(persist_dir=INDEX_PERSIST_DIR)

        cache[file_path] = current_hash
        _save_hash_cache(cache)

        return {"status": "indexed", "chunks": len(docs), "file": file_path, "doc_type": doc_type}

    except Exception as exc:
        return {"status": "error", "message": str(exc), "file": file_path}


def search(
    query: str,
    doc_type: str | None = None,
    top_k: int = 5,
) -> list[dict]:
    """Semantic search across indexed documents.

    Args:
        query:    Natural language query.
        doc_type: Optional filter by document type.
        top_k:    Number of results to return.
    """
    try:
        index, _, _ = _get_index()
        retriever = index.as_retriever(similarity_top_k=top_k)
        nodes = retriever.retrieve(query)

        results = []
        for node in nodes:
            meta = node.metadata or {}
            # Apply doc_type filter client-side
            if doc_type and meta.get("doc_type") != doc_type:
                continue
            results.append({
                "source":  meta.get("file_name", "unknown"),
                "page":    meta.get("page_label"),
                "score":   round(node.score or 0.0, 4),
                "text":    node.text[:800],  # cap per-chunk output
                "doc_type": meta.get("doc_type"),
            })
        return results[:top_k]

    except Exception as exc:
        return [{"error": str(exc)}]
