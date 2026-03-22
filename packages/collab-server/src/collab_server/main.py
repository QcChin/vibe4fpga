"""collab-server FastAPI application.

Endpoints:
  POST /token              — Issue JWT (API key → Bearer token)
  POST /admin/api-key      — Register a new API key (admin only)

  POST /knowledge/index    — Add document to shared knowledge base
  GET  /knowledge/search   — Search knowledge base
  GET  /knowledge/collections — List collections (admin)

  POST /agent/run          — Trigger agent loop remotely
  GET  /agent/{run_id}     — Poll agent loop result

Design doc reference: Phase 4 — 团队协作 + 企业部署
"""

from __future__ import annotations

import asyncio
import os
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .auth import create_token, get_current_user, register_api_key, require_role

app = FastAPI(title="FPGA Vibe Collab Server", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("COLLAB_ALLOW_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory agent run store (replace with Redis/DB for production)
_agent_runs: dict[str, dict] = {}


# ── Auth endpoints ─────────────────────────────────────────────────────────────

class TokenRequest(BaseModel):
    api_key: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 604800   # 7 days in seconds


@app.post("/token", response_model=TokenResponse)
async def issue_token(req: TokenRequest) -> TokenResponse:
    """Exchange an API key for a JWT bearer token."""
    import hashlib
    from .auth import _API_KEYS, hash_api_key

    key_hash = hash_api_key(req.api_key)
    if key_hash not in _API_KEYS:
        raise HTTPException(status_code=401, detail="Invalid API key")

    info  = _API_KEYS[key_hash]
    token = create_token(user_id=info["user_id"], role=info["role"])
    return TokenResponse(access_token=token)


class ApiKeyRequest(BaseModel):
    user_id: str
    role: str = "contributor"


@app.post("/admin/api-key")
async def create_api_key(
    req: ApiKeyRequest,
    user: dict = Depends(require_role("admin")),
) -> dict:
    """Generate and register a new API key (admin only)."""
    raw_key = secrets.token_urlsafe(32)
    register_api_key(raw_key, req.user_id, req.role)
    return {"api_key": raw_key, "user_id": req.user_id, "role": req.role}


# ── Knowledge base endpoints ──────────────────────────────────────────────────

class IndexRequest(BaseModel):
    content:  str
    doc_id:   str
    scope:    str = "global"          # "global" | "project" | "private"
    scope_id: str | None = None
    metadata: dict = {}


class SearchRequest(BaseModel):
    query:    str
    scope:    str = "global"
    scope_id: str | None = None
    top_k:    int = 5


@app.post("/knowledge/index")
async def index_doc(
    req: IndexRequest,
    user: dict = Depends(require_role("contributor")),
) -> dict:
    """Add or update a document in the team knowledge base."""
    from .knowledge import index_document

    # Private scope: force scope_id to current user
    scope_id = req.scope_id
    if req.scope == "private":
        scope_id = user["user_id"]

    return await index_document(
        content=req.content,
        doc_id=req.doc_id,
        scope=req.scope,
        scope_id=scope_id,
        metadata=req.metadata,
        user_id=user["user_id"],
    )


@app.get("/knowledge/search")
async def search_docs(
    query: str,
    scope: str = "global",
    scope_id: str | None = None,
    top_k: int = 5,
    user: dict = Depends(get_current_user),
) -> list[dict]:
    """Search the knowledge base."""
    from .knowledge import search_knowledge

    # Private scope: enforce own user_id
    if scope == "private":
        scope_id = user["user_id"]

    return await search_knowledge(
        query=query,
        scope=scope,
        scope_id=scope_id,
        top_k=top_k,
    )


@app.get("/knowledge/collections")
async def list_knowledge_collections(
    user: dict = Depends(require_role("admin")),
) -> list[dict]:
    """List all collections (admin only)."""
    from .knowledge import list_collections
    return await list_collections()


# ── Agent loop endpoints ──────────────────────────────────────────────────────

class AgentRunRequest(BaseModel):
    goal:       str
    context:    dict = {}
    model:      str = "claude"
    max_rounds: int = 3
    router_url: str = "http://localhost:8765"


@app.post("/agent/run")
async def start_agent_run(
    req: AgentRunRequest,
    user: dict = Depends(require_role("contributor")),
) -> dict:
    """Start an autonomous agent loop in the background.

    Returns a run_id that can be polled via GET /agent/{run_id}.
    """
    run_id = str(uuid.uuid4())
    _agent_runs[run_id] = {
        "run_id":    run_id,
        "status":    "running",
        "goal":      req.goal,
        "user_id":   user["user_id"],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "result":    None,
    }

    async def _run():
        try:
            from skills.agent_loop import run as agent_run
            result = await agent_run(
                goal=req.goal,
                context={**req.context, "user_id": user["user_id"]},
                router_url=req.router_url,
                model=req.model,
                max_rounds=req.max_rounds,
            )
            _agent_runs[run_id]["status"] = "done" if result["success"] else "failed"
            _agent_runs[run_id]["result"] = result
        except Exception as exc:
            _agent_runs[run_id]["status"] = "error"
            _agent_runs[run_id]["error"]  = str(exc)

    asyncio.create_task(_run())
    return {"run_id": run_id, "status": "running"}


@app.get("/agent/{run_id}")
async def get_agent_run(
    run_id: str,
    user: dict = Depends(get_current_user),
) -> dict:
    """Poll the status and result of an agent loop run."""
    run = _agent_runs.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "version": "0.1.0",
        "team_id": os.getenv("COLLAB_TEAM_ID", "default"),
    }


def start() -> None:
    import uvicorn
    port = int(os.getenv("COLLAB_PORT", "8766"))
    uvicorn.run("collab_server.main:app", host="0.0.0.0", port=port, reload=False)
