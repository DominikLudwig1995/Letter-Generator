"""A saved Letter -- everything LetterRequest (app/main.py) needs to
regenerate the same PDF later, plus a label for the list view.

Deliberately flat rather than nesting the custom-sender fields in a
separate table: a letter has at most one, it's never queried on its
own, and SQLModel/SQLAlchemy relationship plumbing (see cv-service's
models.py for how much that costs) buys nothing here.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlmodel import Field, SQLModel

from app.latex import DEFAULT_ANREDE, DEFAULT_GRUSSFORMEL


class Letter(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)

    sender_id: str
    custom_sender_name: str | None = None
    custom_sender_strasse: str | None = None
    custom_sender_ort: str | None = None
    custom_sender_zusatz: str | None = None
    custom_sender_land: str | None = None
    custom_sender_email: str | None = None

    recipient_name: str
    recipient_strasse: str = ""
    recipient_ort: str = ""
    subject: str
    body: str
    letter_date: date | None = None
    # PDF language: "de" or "en" -- see app/latex.py's SUPPORTED_LANGUAGES.
    language: str = "de"
    anrede: str = DEFAULT_ANREDE
    grussformel: str = DEFAULT_GRUSSFORMEL
    bestellnummer: str | None = None

    # Filename under BRIEF_SIGNATURE_DIR, not the PNG bytes themselves --
    # same reasoning as cv-service's Profile.photo_path.
    signature_path: str | None = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
