"""Tests for app/paperless.py's upload_pdf -- the outbound HTTP call to
Paperless-ngx is mocked via httpx's MockTransport, not a real network
call."""

import httpx
import pytest

import app.paperless as paperless


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    """Most tests want PAPERLESS_URL/PAPERLESS_API_TOKEN set -- opt out
    with the `_unconfigured` fixture below where that matters."""
    monkeypatch.setattr(paperless, "PAPERLESS_URL", "https://paperless.example.test")
    monkeypatch.setattr(paperless, "PAPERLESS_API_TOKEN", "test-token-123")


_RealAsyncClient = httpx.AsyncClient


def _client_with_transport(transport: httpx.MockTransport):
    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return _RealAsyncClient(*args, **kwargs)

    return factory


class TestUploadPdf:
    async def test_happy_path_returns_task_id(self, monkeypatch):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["request"] = request
            return httpx.Response(200, json="3fa2-fake-task-id")

        monkeypatch.setattr(
            paperless.httpx, "AsyncClient", _client_with_transport(httpx.MockTransport(handler))
        )

        task_id = await paperless.upload_pdf(b"%PDF-fake", title="Testbrief", filename="a.pdf")

        assert task_id == "3fa2-fake-task-id"
        req = captured["request"]
        assert req.url == "https://paperless.example.test/api/documents/post_document/"
        assert req.headers["authorization"] == "Token test-token-123"

    async def test_not_configured_raises_without_http_call(self, monkeypatch):
        monkeypatch.setattr(paperless, "PAPERLESS_URL", "")
        monkeypatch.setattr(paperless, "PAPERLESS_API_TOKEN", "")

        with pytest.raises(paperless.PaperlessNotConfigured):
            await paperless.upload_pdf(b"%PDF", title="X", filename="x.pdf")

    async def test_partially_configured_raises(self, monkeypatch):
        monkeypatch.setattr(paperless, "PAPERLESS_API_TOKEN", "")

        with pytest.raises(paperless.PaperlessNotConfigured):
            await paperless.upload_pdf(b"%PDF", title="X", filename="x.pdf")

    async def test_auth_failure_raises_upload_error_with_status(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, text="Invalid token")

        monkeypatch.setattr(
            paperless.httpx, "AsyncClient", _client_with_transport(httpx.MockTransport(handler))
        )

        with pytest.raises(paperless.PaperlessUploadError) as exc_info:
            await paperless.upload_pdf(b"%PDF", title="X", filename="x.pdf")
        assert exc_info.value.status_code == 403

    async def test_server_error_raises_upload_error(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="internal error")

        monkeypatch.setattr(
            paperless.httpx, "AsyncClient", _client_with_transport(httpx.MockTransport(handler))
        )

        with pytest.raises(paperless.PaperlessUploadError) as exc_info:
            await paperless.upload_pdf(b"%PDF", title="X", filename="x.pdf")
        assert exc_info.value.status_code == 500

    async def test_unreachable_raises_upload_error_without_status(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(
            paperless.httpx, "AsyncClient", _client_with_transport(httpx.MockTransport(handler))
        )

        with pytest.raises(paperless.PaperlessUploadError) as exc_info:
            await paperless.upload_pdf(b"%PDF", title="X", filename="x.pdf")
        assert exc_info.value.status_code is None
