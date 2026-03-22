"""FastAPI server for the cwtwb Tableau Dashboard Extension.

Endpoints:
    POST /api/suggest  — receive schema + prompt + optional image → dashboard plan
    POST /api/generate — receive schema + data + plan → .twbx file download
    GET  /api/status/{id} — poll generation progress

In production, also serves the built Vite frontend as static files.
"""

from __future__ import annotations

import logging
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .chart_suggestion import dict_to_suggestion, suggest_dashboard
from .image_analysis import analyze_reference_image
from .pipeline import generate_workbook
from .schema_inference import TableauField

# Configure logging so all warnings/errors are visible on the console
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s [%(name)s] %(message)s",
)

# Load .env file if present (for API keys)
_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and value:
                # Use direct assignment — setdefault won't overwrite
                # empty strings left by prior `setx` / `set` commands
                if not os.environ.get(key):
                    os.environ[key] = value


class SuggestRequest(BaseModel):
    fields: list[dict]
    prompt: str = ""
    row_count: int = 0
    max_charts: int = 8
    image_base64: str = ""
    sample_rows: list[list[Any]] = []


class GenerateRequest(BaseModel):
    fields: list[dict]
    data_rows: list[list[Any]]
    plan: dict

logger = logging.getLogger(__name__)

app = FastAPI(
    title="cwtwb Dashboard Extension",
    description="Generate Tableau dashboards from connected data",
    version="0.1.0",
)

# CORS for Tableau extension and Vite dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-progress generation tracking
_jobs: dict[str, dict] = {}


@app.post("/api/suggest")
async def suggest(req: SuggestRequest) -> JSONResponse:
    """Suggest a dashboard layout from field schema and prompt."""
    tableau_fields = [
        TableauField(
            name=f["name"],
            datatype=f.get("datatype", "string"),
            role=f.get("role", ""),
            cardinality=f.get("cardinality", 0),
            sample_values=f.get("sample_values", []),
            null_count=f.get("null_count", 0),
        )
        for f in req.fields
    ]

    image_analysis = None
    if req.image_base64:
        image_analysis = analyze_reference_image(image_base64=req.image_base64)

    plan = suggest_dashboard(
        fields=tableau_fields,
        row_count=req.row_count,
        prompt=req.prompt,
        image_analysis=image_analysis,
        max_charts=req.max_charts,
        sample_rows=req.sample_rows or None,
    )

    # Tag response with engine info — if _warning is set, LLM failed
    has_api_key = bool(
        os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")
    )
    # If plan already has _warning from suggest_dashboard, LLM failed
    if plan.get("_warning"):
        plan["_engine"] = "rules"
    elif has_api_key:
        plan["_engine"] = "llm"
    else:
        plan["_engine"] = "rules"
        plan["_warning"] = (
            "No LLM API key configured. Dashboard was generated using rule-based "
            "fallback. Set ANTHROPIC_API_KEY via the API Key Settings panel "
            "for AI-powered dashboards."
        )

    return JSONResponse(content=plan)


@app.post("/api/generate")
async def generate(req: GenerateRequest) -> FileResponse:
    """Generate a .twbx workbook from data and a dashboard plan."""
    job_id = uuid.uuid4().hex[:8]
    _jobs[job_id] = {"status": "running", "progress": 0}

    try:
        tableau_fields = [
            TableauField(
                name=f["name"],
                datatype=f.get("datatype", "string"),
                role=f.get("role", ""),
                cardinality=f.get("cardinality", 0),
                null_count=f.get("null_count", 0),
            )
            for f in req.fields
        ]

        _jobs[job_id]["progress"] = 20
        output_path = generate_workbook(
            fields=tableau_fields,
            data_rows=req.data_rows,
            plan=req.plan,
        )

        _jobs[job_id] = {"status": "completed", "progress": 100, "path": output_path}

        filename = Path(output_path).name
        return FileResponse(
            path=output_path,
            media_type="application/octet-stream",
            filename=filename,
        )
    except Exception as exc:
        logger.exception("Generate failed: %s", exc)
        _jobs[job_id] = {"status": "failed", "error": str(exc)}
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/status/{job_id}")
async def status(job_id: str) -> JSONResponse:
    """Check generation progress."""
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return JSONResponse(content=_jobs[job_id])


class SetKeyRequest(BaseModel):
    api_key: str


@app.post("/api/set-key")
async def set_key(req: SetKeyRequest) -> JSONResponse:
    """Store the API key in the process environment."""
    key = req.api_key.strip()
    if not key:
        raise HTTPException(status_code=400, detail="API key must not be empty")
    os.environ["ANTHROPIC_API_KEY"] = key
    logger.info("ANTHROPIC_API_KEY updated via /api/set-key")
    return JSONResponse(content={"ok": True})


@app.get("/api/key-status")
async def key_status() -> JSONResponse:
    """Return whether an API key is configured (never expose the key itself)."""
    configured = bool(
        os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")
    )
    return JSONResponse(content={"configured": configured})


@app.get("/api/health")
async def health() -> JSONResponse:
    """Health check endpoint."""
    return JSONResponse(content={"status": "ok", "version": "0.1.0"})


# Mount built frontend in production
_frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if _frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="frontend")


def main():
    """Run the extension server."""
    import uvicorn

    port = int(os.environ.get("CWTWB_EXT_PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
