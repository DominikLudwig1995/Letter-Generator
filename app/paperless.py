"""Upload a generated letter PDF to Paperless-ngx.

Reuses the existing paperless-gpt integration's API token (the same
Paperless instance already trusts it with full API access) rather than
minting a new dedicated credential -- explicitly what was asked for. A
tighter-scoped, brief-specific token would be a reasonable follow-up if
this token's broad access ever becomes a concern, but wasn't requested
and this reuses infrastructure the cluster already has in place.
"""

from __future__ import annotations

import os

import httpx

PAPERLESS_URL = os.environ.get("PAPERLESS_URL", "").rstrip("/")
PAPERLESS_API_TOKEN = os.environ.get("PAPERLESS_API_TOKEN", "")


class PaperlessNotConfigured(RuntimeError):
    """PAPERLESS_URL/PAPERLESS_API_TOKEN aren't set on this deployment --
    distinct from an upload actually failing, so the caller can surface
    a clearer message than a generic connection error."""


class PaperlessUploadError(RuntimeError):
    """Paperless was reachable but rejected the upload, or wasn't
    reachable at all -- status_code is None for the latter."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


async def upload_pdf(pdf_bytes: bytes, title: str, filename: str) -> str:
    """POST the PDF to Paperless's consumption endpoint. Returns the task
    ID Paperless assigns for async processing -- post_document queues
    the file for its consumer to pick up; the document doesn't exist as
    a queryable document yet by the time this returns, only the task
    does, so a task ID (not a document ID) is genuinely the right thing
    to hand back to the caller.
    """
    if not PAPERLESS_URL or not PAPERLESS_API_TOKEN:
        raise PaperlessNotConfigured("Paperless upload is not configured on this server")

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(
                f"{PAPERLESS_URL}/api/documents/post_document/",
                headers={"Authorization": f"Token {PAPERLESS_API_TOKEN}"},
                files={"document": (filename, pdf_bytes, "application/pdf")},
                data={"title": title},
            )
        except httpx.HTTPError as exc:
            raise PaperlessUploadError(f"could not reach Paperless: {exc}") from exc

    if resp.status_code >= 400:
        raise PaperlessUploadError(
            f"Paperless rejected the upload (HTTP {resp.status_code}): {resp.text[:300]}",
            status_code=resp.status_code,
        )

    # post_document's success response body is the bare task UUID as a
    # quoted JSON string, e.g. '"3fa2...-...-...-..."'.
    return resp.json()
