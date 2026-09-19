"""FastAPI surface over clause.pipeline. PLAN §7 T12.

    GET  /health            -> {"status": "ok", "provider": ..., "model": ...}
    POST /analyze           multipart `file` (PDF) -> Analysis JSON
    POST /analyze/stream    same upload -> Server-Sent Events:
                              event: stage   data: {"stage","event","seconds"}
                              event: result  data: <Analysis JSON>
                              event: error   data: {"detail": "..."}
    GET  /                  -> web/index.html (the frontend, no build step)

No auth, no persistence, no job store: one upload is one request. The
pipeline runs in a worker thread so the event loop keeps serving.

Run:  uvicorn api.main:app --reload
"""

from __future__ import annotations

import json
import logging
import queue
import tempfile
import threading
from pathlib import Path

import anyio
from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from clause import config
from clause.client import ModelClient, get_client
from clause.pipeline import analyze
from clause.types import Analysis

log = logging.getLogger(__name__)

WEB_DIR = config.REPO_ROOT / "web"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

app = FastAPI(title="Clause", version="0.1.0")


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def model_client(request: Request, fresh: bool = Form(False)) -> ModelClient:
    """One client per process, created on first use so importing the app needs no API key.
    `fresh=true` in the form selects a second client that skips cache reads (but still
    writes), so the document is re-analyzed live and the new result replaces the cached one.
    Tests override this dependency with a MockClient."""
    attr = "fresh_client" if fresh else "client"
    client = getattr(request.app.state, attr, None)
    if client is None:
        client = get_client(cache_read=not fresh)
        setattr(request.app.state, attr, client)
    return client


async def pdf_upload(file: UploadFile) -> bytes:
    """Read and validate the upload. 400 on anything that isn't a digital PDF."""
    name = file.filename or ""
    if not name.lower().endswith(".pdf"):
        raise HTTPException(400, "upload must be a .pdf file")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, f"file exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "file is not a PDF (missing %PDF header)")
    return data


def _run(data: bytes, client: ModelClient, progress=None) -> Analysis:
    """The pipeline wants a path; hand it a temp file that is gone when we return."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "upload.pdf"
        path.write_bytes(data)
        return analyze(path, client, progress=progress)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "provider": config.PROVIDER, "model": config.default_model()}


@app.post("/analyze")
async def analyze_pdf(data: bytes = Depends(pdf_upload),
                      client: ModelClient = Depends(model_client)) -> dict:
    analysis = await run_in_threadpool(_run, data, client)
    return analysis.to_dict()


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


@app.post("/analyze/stream")
async def analyze_pdf_stream(data: bytes = Depends(pdf_upload),
                             client: ModelClient = Depends(model_client)) -> StreamingResponse:
    """Stage-by-stage progress as SSE, then the full Analysis as the final event."""
    q: queue.Queue[tuple[str, dict] | None] = queue.Queue()

    def progress(stage: str, event: str, seconds: float | None) -> None:
        q.put(("stage", {"stage": stage, "event": event, "seconds": seconds}))

    def work() -> None:
        try:
            q.put(("result", _run(data, client, progress).to_dict()))
        except Exception as e:  # noqa: BLE001 — surface anything to the client, then stop
            log.exception("analysis failed")
            q.put(("error", {"detail": f"{type(e).__name__}: {e}"}))
        finally:
            q.put(None)

    threading.Thread(target=work, daemon=True).start()

    async def events():
        while True:
            item = await anyio.to_thread.run_sync(q.get)
            if item is None:
                return
            yield _sse(*item)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/")
def index() -> FileResponse:
    page = WEB_DIR / "index.html"
    if not page.is_file():
        raise HTTPException(404, "web/index.html not built yet")
    return FileResponse(page)
