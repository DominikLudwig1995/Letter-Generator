"""Brief -- generates standard letters (g-brief LaTeX) from a small web
form: pick a sender (or provide a one-off custom one), fill in
recipient name/street/city, subject/text, an optional date, preview the
PDF, download it. Salutation and closing stay fixed defaults -- see
templates/letter.tex.j2.

Letters can also be saved and edited later (app/letters.py) -- this
module keeps the original one-off generate/upload flow, which never
touches the database.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app.db import init_db
from app.latex import LatexCompileError
from app.letters import router as letters_router
from app.paperless import PaperlessNotConfigured, PaperlessUploadError, upload_pdf
from app.pdfgen import build_pdf_bytes
from app.schemas import LetterRequest
from app.senders import SENDERS


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Brief", lifespan=lifespan)
app.include_router(letters_router)


@app.get("/api/senders")
async def list_senders() -> list[dict]:
    return [{"id": s.id, "name": s.name} for s in SENDERS.values()]


@app.post("/api/generate")
async def generate_letter(req: LetterRequest, download: bool = False) -> Response:
    try:
        pdf_bytes = build_pdf_bytes(req)
    except LatexCompileError as exc:
        # Single-operator tool behind auth (tinyauth) -- surfacing
        # pdflatex's own log tail is genuinely useful for figuring out
        # what went wrong, not a leak to an untrusted audience.
        return JSONResponse(
            status_code=500,
            content={"error": str(exc), "log_tail": exc.log_tail},
        )

    disposition = "attachment" if download else "inline"
    filename = f"brief-{(req.letter_date or date.today()).isoformat()}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
    )


@app.post("/api/upload-to-paperless")
async def upload_to_paperless(req: LetterRequest) -> JSONResponse:
    """Generate the same PDF /api/generate would, then hand it to
    Paperless-ngx's consumption API instead of returning it to the
    browser. Reuses the existing paperless-gpt integration token
    (PAPERLESS_API_TOKEN env var) -- see app/paperless.py.
    """
    try:
        pdf_bytes = build_pdf_bytes(req)
    except LatexCompileError as exc:
        return JSONResponse(status_code=500, content={"error": str(exc), "log_tail": exc.log_tail})

    letter_date = req.letter_date or date.today()
    filename = f"brief-{letter_date.isoformat()}.pdf"
    try:
        task_id = await upload_pdf(pdf_bytes, title=req.subject, filename=filename)
    except PaperlessNotConfigured as exc:
        return JSONResponse(status_code=501, content={"error": str(exc)})
    except PaperlessUploadError as exc:
        return JSONResponse(
            status_code=502,
            content={"error": str(exc), "paperless_status_code": exc.status_code},
        )

    return JSONResponse(status_code=200, content={"status": "queued", "task_id": task_id})


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse("static/index.html")


app.mount("/static", StaticFiles(directory="static"), name="static")
