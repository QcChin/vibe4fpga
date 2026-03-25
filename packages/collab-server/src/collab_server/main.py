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
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .auth import create_token, get_current_user, register_api_key, require_role, verify_api_key
from .knowledge import index_document, list_collections, search_knowledge

logger = logging.getLogger(__name__)

app = FastAPI(title="FPGA Vibe Collab Server", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("COLLAB_ALLOW_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Agent run store with TTL: {run_id: {"expires_at": datetime, "data": dict}}
_AGENT_RUN_TTL_HOURS = int(os.getenv("COLLAB_RUN_TTL_HOURS", "24"))
_agent_runs: dict[str, dict] = {}


def _cleanup_expired_runs() -> None:
    """Evict agent run entries older than TTL."""
    now = datetime.now(timezone.utc)
    expired = [rid for rid, entry in _agent_runs.items()
               if entry.get("expires_at", now) < now]
    for rid in expired:
        del _agent_runs[rid]


# ── Auth endpoints ─────────────────────────────────────────────────────────────

class TokenRequest(BaseModel):
    api_key: str


class TokenResponse(BaseModel):
    access_token: str
    token_type:   str = "bearer"
    expires_in:   int = 604800   # 7 days in seconds


@app.post("/token", response_model=TokenResponse)
async def issue_token(req: TokenRequest) -> TokenResponse:
    """Exchange an API key for a JWT bearer token."""
    info = verify_api_key(req.api_key)
    if info is None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    token = create_token(user_id=info["user_id"], role=info["role"])
    return TokenResponse(access_token=token)


class ApiKeyRequest(BaseModel):
    user_id: str
    role:    str = "contributor"


@app.post("/admin/api-key")
async def create_api_key(
    req: ApiKeyRequest,
    user: dict = Depends(require_role("admin")),
) -> dict:
    """Generate and register a new API key (admin only)."""
    if req.role not in ("viewer", "contributor", "admin"):
        raise HTTPException(status_code=400, detail=f"Invalid role: {req.role}")
    raw_key = secrets.token_urlsafe(32)
    register_api_key(raw_key, req.user_id, req.role)
    return {"api_key": raw_key, "user_id": req.user_id, "role": req.role}


# ── Knowledge base endpoints ──────────────────────────────────────────────────

class IndexRequest(BaseModel):
    content:  str
    doc_id:   str
    scope:    str = "global"
    scope_id: str | None = None
    metadata: dict = Field(default_factory=dict)


@app.post("/knowledge/index")
async def index_doc(
    req: IndexRequest,
    user: dict = Depends(require_role("contributor")),
) -> dict:
    """Add or update a document in the team knowledge base."""
    if req.scope not in ("global", "project", "private"):
        raise HTTPException(status_code=400, detail=f"Invalid scope: {req.scope}")

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
    if scope not in ("global", "project", "private"):
        raise HTTPException(status_code=400, detail=f"Invalid scope: {scope}")

    if scope == "private":
        # Enforce: private scope always scoped to the requesting user
        scope_id = user["user_id"]
    elif scope == "project" and scope_id is None:
        raise HTTPException(
            status_code=400,
            detail="scope_id is required when scope='project'",
        )

    return await search_knowledge(query=query, scope=scope, scope_id=scope_id, top_k=top_k)


@app.get("/knowledge/collections")
async def list_knowledge_collections(
    user: dict = Depends(require_role("admin")),
) -> list[dict]:
    """List all collections (admin only)."""
    return await list_collections()


# ── Agent loop endpoints ──────────────────────────────────────────────────────

class AgentRunRequest(BaseModel):
    goal:       str
    context:    dict = Field(default_factory=dict)
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
    _cleanup_expired_runs()

    run_id = str(uuid.uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(hours=_AGENT_RUN_TTL_HOURS)
    _agent_runs[run_id] = {
        "run_id":     run_id,
        "status":     "running",
        "goal":       req.goal,
        "user_id":    user["user_id"],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": expires_at,
        "result":     None,
    }

    async def _run() -> None:
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
            logger.exception("Agent run %s failed", run_id)
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
    entry = _agent_runs.get(run_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Run not found or expired")
    # Return data without the internal expires_at field
    return {k: v for k, v in entry.items() if k != "expires_at"}


@app.get("/health")
async def health() -> dict:
    return {
        "status":  "ok",
        "version": "0.1.0",
        "team_id": os.getenv("COLLAB_TEAM_ID", "default"),
    }


def start() -> None:
    import uvicorn
    port = int(os.getenv("COLLAB_PORT", "8766"))
    uvicorn.run("collab_server.main:app", host="0.0.0.0", port=port, reload=False)
