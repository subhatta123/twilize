"""FastAPI server for the twilize Tableau Dashboard Extension.

Endpoints:
    POST /api/suggest  — receive schema + prompt + optional image → dashboard plan
    POST /api/generate — receive schema + data + plan → .twbx file download
    GET  /api/status/{id} — poll generation progress

In production, also serves the built Vite frontend as static files.
"""

from __future__ import annotations

import base64
import binascii
import json
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
    # Chart specs the caller wants guaranteed in the output. Each item is a
    # dict matching the mainline ``required_charts`` shape, at minimum
    # ``{"title": "Top 10 Customers by Profit", "chart_type": "Bar",
    #     "shelves": [...]}``. Missing fields are tolerated — the extension
    # prepends these to the plan and lets the downstream validator decide
    # whether each one is constructible.
    required_charts: list[dict] = []


class GenerateRequest(BaseModel):
    fields: list[dict]
    data_rows: list[list[Any]]
    plan: dict
    # Raw base64 (no data-URL prefix) of a reference image whose palette,
    # card styling, and typography should be applied to the final workbook
    # via ``editor.apply_style_reference``. Leave empty to fall back to the
    # older ``theme_colors`` path only.
    reference_image_base64: str = ""
    # Passed through to the pipeline so the manifest can report how many
    # required charts actually landed. The caller is expected to have
    # already prepended these into ``plan["charts"]`` via ``/api/suggest``.
    required_charts: list[dict] = []

logger = logging.getLogger(__name__)

app = FastAPI(
    title="twilize Dashboard Extension",
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

    # Prepend any user-required chart specs so they survive the downstream
    # trim / dedup pass. Dedup by normalized title so we don't end up with
    # "Top 10 Customers by Profit" twice when the LLM already returned one.
    if req.required_charts:
        plan_charts = list(plan.get("charts", []))
        existing_titles = {
            (c.get("title") or "").strip().lower()
            for c in plan_charts
        }
        required_prepend: list[dict] = []
        for rc in req.required_charts:
            rc_title = (rc.get("title") or "").strip().lower()
            if rc_title and rc_title in existing_titles:
                continue
            rc_copy = dict(rc)
            rc_copy["_required"] = True
            required_prepend.append(rc_copy)
        if required_prepend:
            plan["charts"] = required_prepend + plan_charts
        plan["required_charts_requested"] = len(req.required_charts)
        plan["required_charts"] = list(req.required_charts)

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


def _decode_reference_image(image_base64: str) -> str:
    """Decode a base64 reference image to a temp file and return its path.

    Accepts either a bare base64 string or a full ``data:image/...;base64,``
    URL (the Extensions API typically hands back the latter). Returns an
    empty string on any decode failure so the caller can fall back to the
    theme-only path without aborting the whole generation.
    """
    if not image_base64:
        return ""
    b64 = image_base64
    if b64.startswith("data:"):
        _, _, b64 = b64.partition(",")
    try:
        raw = base64.b64decode(b64, validate=False)
    except (binascii.Error, ValueError) as exc:
        logger.warning("Could not base64-decode reference image: %s", exc)
        return ""
    if not raw:
        return ""
    ext = ".png" if raw[:8].startswith(b"\x89PNG") else ".jpg"
    fd, tmp_path = tempfile.mkstemp(prefix="twilize_ref_", suffix=ext)
    try:
        os.write(fd, raw)
    finally:
        os.close(fd)
    return tmp_path


def _manifest_header_value(manifest: dict) -> str:
    """Base64-encode the manifest so it can ride on a response header.

    Headers are 8-bit ASCII only and most reverse proxies cap them around
    8 KB. We base64-encode the compact JSON so arbitrary field names (even
    non-ASCII) survive, and we don't have to worry about newlines.
    """
    payload = json.dumps(manifest, default=str, separators=(",", ":"))
    return base64.b64encode(payload.encode("utf-8")).decode("ascii")


@app.post("/api/generate")
async def generate(req: GenerateRequest) -> FileResponse:
    """Generate a .twbx workbook from data and a dashboard plan.

    The response body is the raw ``.twbx`` file download.  The generation
    manifest (dashboards, filter scope, style-reference summary, required-
    charts fulfilment, warnings) is surfaced on an ``X-Twilize-Manifest``
    response header as base64-encoded JSON so the frontend can consume it
    without a second round-trip.
    """
    job_id = uuid.uuid4().hex[:8]
    _jobs[job_id] = {"status": "running", "progress": 0}
    ref_tmp_path = ""

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

        ref_tmp_path = _decode_reference_image(req.reference_image_base64)

        # Accept required_charts either on the top-level request or via the
        # plan dict (if /api/suggest already prepended them). Prefer the
        # top-level value so the manifest's "fulfilled" count matches what
        # the caller actually asked for.
        required_charts = req.required_charts or req.plan.get("required_charts") or []

        _jobs[job_id]["progress"] = 20
        result = generate_workbook(
            fields=tableau_fields,
            data_rows=req.data_rows,
            plan=req.plan,
            reference_image_path=ref_tmp_path,
            required_charts=required_charts,
        )
        output_path = result["output_path"]
        manifest = result.get("manifest", {})
        manifest["job_id"] = job_id

        _jobs[job_id] = {
            "status": "completed",
            "progress": 100,
            "path": output_path,
            "manifest": manifest,
        }

        filename = Path(output_path).name
        return FileResponse(
            path=output_path,
            media_type="application/octet-stream",
            filename=filename,
            headers={
                "X-Twilize-Manifest": _manifest_header_value(manifest),
                "X-Twilize-Job-Id": job_id,
                # Expose custom headers so browser JS can read them (CORS).
                "Access-Control-Expose-Headers": (
                    "X-Twilize-Manifest, X-Twilize-Job-Id"
                ),
            },
        )
    except Exception as exc:
        logger.exception("Generate failed: %s", exc)
        _jobs[job_id] = {"status": "failed", "error": str(exc)}
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if ref_tmp_path and os.path.exists(ref_tmp_path):
            try:
                os.unlink(ref_tmp_path)
            except OSError:
                pass


@app.get("/api/manifest/{job_id}")
async def manifest(job_id: str) -> JSONResponse:
    """Return the generation manifest for a completed job.

    Complementary to ``/api/generate`` for clients that can't read custom
    response headers (some mobile webviews, proxies that strip them, ...).
    """
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") != "completed":
        return JSONResponse(
            content={
                "status": job.get("status"),
                "progress": job.get("progress"),
                "error": job.get("error"),
            }
        )
    return JSONResponse(content=job.get("manifest", {}))


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
