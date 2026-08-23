"""Turns a LetterRequest into compiled PDF bytes. Shared by
/api/generate, /api/upload-to-paperless and the persisted-letters
router's PDF endpoint (app/letters.py) -- three different ways to
arrive at the same "render + compile" step.
"""

from __future__ import annotations

from datetime import date

from fastapi import HTTPException

from app.latex import compile_pdf, render_letter_tex
from app.schemas import LetterRequest, decode_signature_png
from app.senders import CUSTOM_SENDER_ID, Sender, get_sender


def resolve_sender(req: LetterRequest) -> Sender:
    if req.sender_id == CUSTOM_SENDER_ID:
        c = req.custom_sender
        assert c is not None  # guaranteed by LetterRequest's model_validator
        return Sender(
            id=CUSTOM_SENDER_ID,
            name=c.name,
            strasse=c.strasse,
            ort=c.ort,
            zusatz=c.zusatz,
            land=c.land,
            email=c.email,
        )
    return get_sender(req.sender_id)


def build_pdf_bytes(req: LetterRequest) -> bytes:
    """Resolve the sender, decode the signature if present, render the
    LaTeX and compile it. Raises HTTPException(400) for a bad
    sender/signature, and LatexCompileError (left uncaught) for a
    pdflatex failure -- each caller decides how it wants to report that
    one.
    """
    try:
        sender = resolve_sender(req)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown sender: {req.sender_id!r}") from None

    signature_png: bytes | None = None
    if req.signature:
        try:
            signature_png = decode_signature_png(req.signature)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    tex_source = render_letter_tex(
        sender=sender,
        recipient_name=req.recipient_name,
        recipient_strasse=req.recipient_strasse,
        recipient_ort=req.recipient_ort,
        subject=req.subject,
        body=req.body,
        letter_date=req.letter_date or date.today(),
        has_signature=signature_png is not None,
        anrede=req.anrede,
        grussformel=req.grussformel,
        bestellnummer=req.bestellnummer,
        language=req.language,
    )

    return compile_pdf(tex_source, signature_png=signature_png)
