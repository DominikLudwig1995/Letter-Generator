"""SQLite persistence for saved letters. Everything lives under
BRIEF_DATA_DIR, outside the application code directory, so a redeploy
(e.g. `git pull` over the checkout) never touches saved data."""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from app.latex import DEFAULT_ANREDE, DEFAULT_GRUSSFORMEL

DATA_DIR = Path(os.environ.get("BRIEF_DATA_DIR", "./data"))
DB_PATH = Path(os.environ.get("BRIEF_DB_PATH", str(DATA_DIR / "brief.db")))
SIGNATURE_DIR = Path(os.environ.get("BRIEF_SIGNATURE_DIR", str(DATA_DIR / "signatures")))

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})

# Columns added after the "letter" table already existed in deployed
# databases. create_all() below only creates missing *tables*, never
# adds columns to one that's already there, so a plain redeploy would
# leave old databases without these and every saved-letter read/write
# would 500 on the missing column. Cheap and idempotent enough not to
# warrant a real migration framework for a single-table personal app.
_ADDED_COLUMNS = [
    ("anrede", f"TEXT NOT NULL DEFAULT '{DEFAULT_ANREDE}'"),
    ("grussformel", f"TEXT NOT NULL DEFAULT '{DEFAULT_GRUSSFORMEL}'"),
    ("bestellnummer", "TEXT"),  # nullable/optional, no default needed
    ("language", "TEXT NOT NULL DEFAULT 'de'"),
]


def _add_missing_columns() -> None:
    with engine.begin() as conn:
        existing = {row[1] for row in conn.execute(text("PRAGMA table_info(letter)"))}
        for column, ddl in _ADDED_COLUMNS:
            if column not in existing:
                conn.execute(text(f"ALTER TABLE letter ADD COLUMN {column} {ddl}"))


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SIGNATURE_DIR.mkdir(parents=True, exist_ok=True)
    SQLModel.metadata.create_all(engine)
    _add_missing_columns()


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
