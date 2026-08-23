"""Default sender profiles, plus support for a one-off custom sender.

Quick-pick presets exist so the common case (sending a letter as
yourself) is one click instead of retyping your address every time.
Configure your own via the BRIEF_SENDERS_JSON environment variable --
a JSON object mapping an id to {name, strasse, ort, zusatz, land,
email} (zusatz/land/email optional) -- e.g.:

    export BRIEF_SENDERS_JSON='{"me": {"name": "Jane Doe", "strasse": "Main St 1", "ort": "12345 Anytown"}}'

Falls back to a single generic example sender if unset, so the app
still runs out of the box. A third, freeform sender (CUSTOM_SENDER_ID)
lets any other name/address be used for a one-off letter without
editing config at all -- see app/main.py's LetterRequest, which
requires a full CustomSender payload only when sender_id == "custom".
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Sender:
    id: str
    name: str
    strasse: str
    ort: str
    zusatz: str = ""
    land: str = ""
    email: str = ""


def _load_senders() -> dict[str, Sender]:
    raw = os.environ.get("BRIEF_SENDERS_JSON")
    if not raw:
        return {
            "example": Sender(
                id="example",
                name="Jane Doe",
                strasse="Main Street 1",
                ort="12345 Anytown",
            ),
        }
    parsed = json.loads(raw)
    return {sender_id: Sender(id=sender_id, **fields) for sender_id, fields in parsed.items()}


SENDERS: dict[str, Sender] = _load_senders()

DEFAULT_SENDER_ID = next(iter(SENDERS))
CUSTOM_SENDER_ID = "custom"


def get_sender(sender_id: str) -> Sender:
    try:
        return SENDERS[sender_id]
    except KeyError:
        raise ValueError(f"Unknown sender: {sender_id!r}") from None
