"""CRUD for saved letters, plus a PDF endpoint that regenerates the
same document /api/generate would from the saved fields. Signature
PNGs are stored as files under BRIEF_SIGNATURE_DIR (app/db.py), not in
the database -- same reasoning as cv-service's photo uploads.
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlmodel import Session, select

from app.db import SIGNATURE_DIR, get_session
from app.latex import LatexCompileError, default_anrede, default_grussformel
from app.models import Letter
from app.pdfgen import build_pdf_bytes
from app.schemas import CustomSender, LetterRequest, decode_signature_png

router = APIRouter(tags=["Letters"])


def _letter_or_404(session: Session, letter_id: int) -> Letter:
    letter = session.get(Letter, letter_id)
    if letter is None:
        raise HTTPException(404, f"letter {letter_id} not found")
    return letter


def _read_signature_data_url(letter: Letter) -> str | None:
    if not letter.signature_path:
        return None
    path = SIGNATURE_DIR / letter.signature_path
    if not path.is_file():
        return None
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _letter_to_request(letter: Letter) -> LetterRequest:
    """Rebuilds the same shape /api/generate accepts, so the PDF
    endpoint below can reuse build_pdf_bytes unchanged."""
    custom_sender = None
    if letter.sender_id == "custom":
        custom_sender = CustomSender(
            name=letter.custom_sender_name or "",
            strasse=letter.custom_sender_strasse or "",
            ort=letter.custom_sender_ort or "",
            zusatz=letter.custom_sender_zusatz or "",
            land=letter.custom_sender_land or "",
            email=letter.custom_sender_email or "",
        )
    return LetterRequest(
        sender_id=letter.sender_id,
        custom_sender=custom_sender,
        recipient_name=letter.recipient_name,
        recipient_strasse=letter.recipient_strasse,
        recipient_ort=letter.recipient_ort,
        subject=letter.subject,
        body=letter.body,
        letter_date=letter.letter_date,
        language=letter.language,
        anrede=letter.anrede,
        grussformel=letter.grussformel,
        bestellnummer=letter.bestellnummer,
        signature=_read_signature_data_url(letter),
    )


def _apply_request_to_letter(letter: Letter, req: LetterRequest, session: Session) -> None:
    letter.sender_id = req.sender_id
    if req.sender_id == "custom" and req.custom_sender:
        letter.custom_sender_name = req.custom_sender.name
        letter.custom_sender_strasse = req.custom_sender.strasse
        letter.custom_sender_ort = req.custom_sender.ort
        letter.custom_sender_zusatz = req.custom_sender.zusatz
        letter.custom_sender_land = req.custom_sender.land
        letter.custom_sender_email = req.custom_sender.email
    else:
        letter.custom_sender_name = None
        letter.custom_sender_strasse = None
        letter.custom_sender_ort = None
        letter.custom_sender_zusatz = None
        letter.custom_sender_land = None
        letter.custom_sender_email = None

    letter.recipient_name = req.recipient_name
    letter.recipient_strasse = req.recipient_strasse
    letter.recipient_ort = req.recipient_ort
    letter.subject = req.subject
    letter.body = req.body
    letter.letter_date = req.letter_date
    letter.language = req.language
    # Persist the *resolved* text, not a bare None -- Letter.anrede/
    # grussformel are non-nullable columns, and a saved letter needs a
    # concrete value to redisplay in the form on load, same as what
    # actually ended up in the PDF when it was generated.
    letter.anrede = req.anrede if req.anrede is not None else default_anrede(req.language)
    letter.grussformel = (
        req.grussformel if req.grussformel is not None else default_grussformel(req.language)
    )
    letter.bestellnummer = req.bestellnummer

    # Only touches the stored signature if the client actually sent a
    # new one -- re-saving an edited letter without redrawing the
    # signature pad (the common case) must not silently drop it.
    if req.signature:
        try:
            png = decode_signature_png(req.signature)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        if letter.signature_path:
            old = SIGNATURE_DIR / letter.signature_path
            old.unlink(missing_ok=True)
        filename = f"{uuid.uuid4().hex}.png"
        (SIGNATURE_DIR / filename).write_bytes(png)
        letter.signature_path = filename


@router.get("/api/letters")
def list_letters(session: Session = Depends(get_session)) -> list[dict]:
    letters = session.exec(select(Letter).order_by(Letter.updated_at.desc())).all()
    return [
        {
            "id": letter.id,
            "subject": letter.subject,
            "recipient_name": letter.recipient_name,
            "letter_date": letter.letter_date,
            "updated_at": letter.updated_at,
        }
        for letter in letters
    ]


@router.post("/api/letters", status_code=201)
def create_letter(req: LetterRequest, session: Session = Depends(get_session)) -> Letter:
    letter = Letter(
        sender_id=req.sender_id,
        recipient_name=req.recipient_name,
        subject=req.subject,
        body=req.body,
    )
    _apply_request_to_letter(letter, req, session)
    session.add(letter)
    session.commit()
    session.refresh(letter)
    return letter


@router.get("/api/letters/{letter_id}")
def get_letter(letter_id: int, session: Session = Depends(get_session)) -> dict:
    letter = _letter_or_404(session, letter_id)
    return _letter_to_request(letter).model_dump(mode="json") | {"id": letter.id}


@router.put("/api/letters/{letter_id}")
def update_letter(
    letter_id: int, req: LetterRequest, session: Session = Depends(get_session)
) -> Letter:
    letter = _letter_or_404(session, letter_id)
    _apply_request_to_letter(letter, req, session)
    letter.updated_at = datetime.now(UTC)
    session.add(letter)
    session.commit()
    session.refresh(letter)
    return letter


@router.delete("/api/letters/{letter_id}", status_code=204)
def delete_letter(letter_id: int, session: Session = Depends(get_session)) -> None:
    letter = _letter_or_404(session, letter_id)
    if letter.signature_path:
        (SIGNATURE_DIR / letter.signature_path).unlink(missing_ok=True)
    session.delete(letter)
    session.commit()


@router.get("/api/letters/{letter_id}/pdf")
def letter_pdf(
    letter_id: int, download: bool = False, session: Session = Depends(get_session)
) -> Response:
    letter = _letter_or_404(session, letter_id)
    req = _letter_to_request(letter)
    try:
        pdf_bytes = build_pdf_bytes(req)
    except LatexCompileError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    disposition = "attachment" if download else "inline"
    filename = f"brief-{(letter.letter_date or date.today()).isoformat()}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
    )
