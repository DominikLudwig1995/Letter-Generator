"""Request payloads shared by /api/generate, /api/upload-to-paperless
and the persisted-letters router (app/letters.py) -- all three accept
the same shape, since saving a letter is "the /api/generate payload,
kept".
"""

from __future__ import annotations

import base64
import binascii
from datetime import date

from pydantic import BaseModel, Field, field_validator, model_validator

from app.latex import SUPPORTED_LANGUAGES
from app.senders import CUSTOM_SENDER_ID, DEFAULT_SENDER_ID

# Generous but bounded -- this is a letter generator, not a document
# editor. Limits exist to keep pdflatex's compile time/output size sane,
# not because these are expected to be hit in normal use.
MAX_NAME_CHARS = 500
MAX_ADDRESS_LINE_CHARS = 200
MAX_SUBJECT_CHARS = 200
MAX_BODY_CHARS = 20000
MAX_ANREDE_CHARS = 200
MAX_GRUSSFORMEL_CHARS = 200
# An order/customer number is short by convention, not freeform text --
# generous ceiling regardless.
MAX_BESTELLNUMMER_CHARS = 100
# A signature drawn on an on-screen canvas is a small image -- a hard
# ceiling well above what that ever produces (a few tens of KB) still
# rules out someone sending an unrelated multi-megabyte "signature".
MAX_SIGNATURE_BYTES = 2 * 1024 * 1024

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def decode_signature_png(data: str) -> bytes:
    """Decode a client-submitted signature into raw PNG bytes, or raise
    ValueError with a message safe to return to the caller.

    Accepts either a bare base64 string or a data: URL
    ("data:image/png;base64,...."), matching what
    HTMLCanvasElement.toDataURL('image/png') actually produces client-side.
    Validated against the real PNG magic bytes rather than trusted by
    field name alone -- this ends up embedded via \\includegraphics in a
    real pdflatex run, so it should at least really be a PNG before that,
    even though pdflatex itself (not this validation) is the actual
    security boundary against malicious file content.
    """
    if "," in data and data.strip().lower().startswith("data:"):
        data = data.split(",", 1)[1]
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"signature is not valid base64: {exc}") from exc
    if len(raw) > MAX_SIGNATURE_BYTES:
        raise ValueError(f"signature exceeds {MAX_SIGNATURE_BYTES} bytes")
    if not raw.startswith(_PNG_MAGIC):
        raise ValueError("signature is not a PNG image")
    return raw


class CustomSender(BaseModel):
    """A one-off sender not in app/senders.py's two presets. Required in
    full (name/strasse/ort) when LetterRequest.sender_id == 'custom' --
    see LetterRequest's validator."""

    name: str = Field(min_length=1, max_length=MAX_NAME_CHARS)
    strasse: str = Field(min_length=1, max_length=MAX_ADDRESS_LINE_CHARS)
    ort: str = Field(min_length=1, max_length=MAX_ADDRESS_LINE_CHARS)
    zusatz: str = Field(default="", max_length=MAX_ADDRESS_LINE_CHARS)
    land: str = Field(default="", max_length=MAX_ADDRESS_LINE_CHARS)
    email: str = Field(default="", max_length=MAX_ADDRESS_LINE_CHARS)


class LetterRequest(BaseModel):
    sender_id: str = Field(default=DEFAULT_SENDER_ID)
    custom_sender: CustomSender | None = None
    recipient_name: str = Field(min_length=1, max_length=MAX_NAME_CHARS)
    recipient_strasse: str = Field(default="", max_length=MAX_ADDRESS_LINE_CHARS)
    recipient_ort: str = Field(default="", max_length=MAX_ADDRESS_LINE_CHARS)
    subject: str = Field(min_length=1, max_length=MAX_SUBJECT_CHARS)
    body: str = Field(min_length=1, max_length=MAX_BODY_CHARS)
    # Controls both the PDF's own language (g-brief's [ngerman]/[english]
    # class option, section labels, date format) and, when anrede/
    # grussformel are omitted, which language their defaults come from.
    language: str = Field(default="de")
    # Both configurable per letter. Left as None (rather than a fixed
    # German default) so omitting them picks up the *language-appropriate*
    # default in render_letter_tex -- a fixed default here would always
    # be German regardless of `language`.
    anrede: str | None = Field(default=None, min_length=1, max_length=MAX_ANREDE_CHARS)
    grussformel: str | None = Field(default=None, min_length=1, max_length=MAX_GRUSSFORMEL_CHARS)
    # Defaults to today (set in generate_letter, not here) when omitted --
    # a bare `date | None = None` default keeps "not specified" and "the
    # client is somehow sending null" indistinguishable from "today" was
    # explicitly meant, which does not matter for this field either way.
    letter_date: date | None = None
    # Base64 PNG (or a data: URL) from the frontend's signature canvas.
    # Optional -- without it, \Unterschrift falls back to the sender's
    # typed name only, same as before this field existed.
    signature: str | None = None
    # Optional order/customer number -- prepended as its own line above
    # the subject when given, omitted entirely otherwise. See
    # render_letter_tex's docstring for why it isn't g-brief's own
    # \IhrZeichen reference-line field.
    bestellnummer: str | None = Field(default=None, max_length=MAX_BESTELLNUMMER_CHARS)

    @model_validator(mode="after")
    def _custom_sender_required_iff_custom_id(self) -> LetterRequest:
        if self.sender_id == CUSTOM_SENDER_ID and self.custom_sender is None:
            raise ValueError("custom_sender is required when sender_id is 'custom'")
        return self

    @field_validator("language")
    @classmethod
    def _language_is_supported(cls, value: str) -> str:
        if value not in SUPPORTED_LANGUAGES:
            raise ValueError(f"language must be one of {SUPPORTED_LANGUAGES}")
        return value
