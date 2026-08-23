"""Tests for the FastAPI endpoints. compile_pdf is mocked here -- the
real compilation path is covered separately in test_latex.py (skipped
when pdflatex isn't installed); these tests are about request handling/
validation, not LaTeX correctness."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.latex import LatexCompileError
from app.main import app
from app.schemas import MAX_SIGNATURE_BYTES, decode_signature_png

# TestClient(app) without a `with` block never fires the lifespan, so
# app.db.init_db() -- which creates BRIEF_DATA_DIR and the sqlite file
# -- never runs. The letters tests below need that database to exist;
# entering the client as a context manager once at import time triggers
# it exactly like a real server startup would.
client = TestClient(app).__enter__()

# A real, minimal 1x1 transparent PNG -- used wherever a test needs
# actual valid PNG bytes rather than a mock, e.g. to exercise the real
# magic-byte validation in decode_signature_png.
TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class TestListSenders:
    def test_returns_the_example_profile(self):
        r = client.get("/api/senders")
        assert r.status_code == 200
        ids = {s["id"] for s in r.json()}
        assert ids == {"example"}


class TestDecodeSignaturePng:
    def test_decodes_bare_base64(self):
        raw = decode_signature_png(TINY_PNG_B64)
        assert raw.startswith(b"\x89PNG")

    def test_decodes_data_url(self):
        raw = decode_signature_png(f"data:image/png;base64,{TINY_PNG_B64}")
        assert raw.startswith(b"\x89PNG")

    def test_rejects_invalid_base64(self):
        try:
            decode_signature_png("not-valid-base64!!!")
            raise AssertionError("expected ValueError")
        except ValueError as exc:
            assert "base64" in str(exc)

    def test_rejects_non_png_content(self):
        import base64

        not_a_png = base64.b64encode(b"just some plain text, not a png").decode()
        try:
            decode_signature_png(not_a_png)
            raise AssertionError("expected ValueError")
        except ValueError as exc:
            assert "PNG" in str(exc)

    def test_rejects_oversized_payload(self):
        import base64

        huge = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * (MAX_SIGNATURE_BYTES + 1)).decode()
        try:
            decode_signature_png(huge)
            raise AssertionError("expected ValueError")
        except ValueError as exc:
            assert "exceeds" in str(exc)


class TestGenerateLetter:
    def _payload(self, **overrides):
        base = {
            "sender_id": "example",
            "recipient_name": "Firma GmbH",
            "recipient_strasse": "Musterstraße 1",
            "recipient_ort": "12345 Stadt",
            "subject": "Testbetreff",
            "body": "Testtext.",
        }
        base.update(overrides)
        return base

    def test_happy_path_returns_pdf(self):
        with patch("app.pdfgen.compile_pdf", return_value=b"%PDF-fake-bytes"):
            r = client.post("/api/generate", json=self._payload())

        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"
        assert r.content == b"%PDF-fake-bytes"

    def test_default_disposition_is_inline_for_preview(self):
        with patch("app.pdfgen.compile_pdf", return_value=b"%PDF"):
            r = client.post("/api/generate", json=self._payload())
        assert "inline" in r.headers["content-disposition"]

    def test_download_flag_sets_attachment_disposition(self):
        with patch("app.pdfgen.compile_pdf", return_value=b"%PDF"):
            r = client.post("/api/generate?download=true", json=self._payload())
        assert "attachment" in r.headers["content-disposition"]

    def test_unknown_sender_returns_400(self):
        r = client.post("/api/generate", json=self._payload(sender_id="nonexistent"))
        assert r.status_code == 400

    def test_missing_recipient_name_returns_422(self):
        payload = self._payload()
        del payload["recipient_name"]
        r = client.post("/api/generate", json=payload)
        assert r.status_code == 422

    def test_empty_subject_returns_422(self):
        r = client.post("/api/generate", json=self._payload(subject=""))
        assert r.status_code == 422

    def test_oversized_body_returns_422(self):
        r = client.post("/api/generate", json=self._payload(body="x" * 20001))
        assert r.status_code == 422

    def test_compile_error_returns_500_with_log_tail(self):
        with patch(
            "app.pdfgen.compile_pdf",
            side_effect=LatexCompileError("boom", log_tail="! Undefined control sequence."),
        ):
            r = client.post("/api/generate", json=self._payload())

        assert r.status_code == 500
        body = r.json()
        assert "boom" in body["error"]
        assert "Undefined control sequence" in body["log_tail"]

    def test_default_sender_used_when_omitted(self):
        payload = self._payload()
        del payload["sender_id"]
        with patch("app.pdfgen.compile_pdf", return_value=b"%PDF") as mock_compile:
            r = client.post("/api/generate", json=payload)
        assert r.status_code == 200
        mock_compile.assert_called_once()

    def test_explicit_date_is_used(self):
        with (
            patch("app.pdfgen.render_letter_tex", return_value="") as mock_render,
            patch("app.pdfgen.compile_pdf", return_value=b"%PDF"),
        ):
            r = client.post("/api/generate", json=self._payload(letter_date="2020-01-01"))
        assert r.status_code == 200
        import datetime

        assert mock_render.call_args.kwargs["letter_date"] == datetime.date(2020, 1, 1)

    def test_omitted_date_defaults_to_today(self):
        with (
            patch("app.pdfgen.render_letter_tex", return_value="") as mock_render,
            patch("app.pdfgen.compile_pdf", return_value=b"%PDF"),
        ):
            r = client.post("/api/generate", json=self._payload())
        assert r.status_code == 200
        import datetime

        assert mock_render.call_args.kwargs["letter_date"] == datetime.date.today()

    def test_omitted_anrede_and_grussformel_pass_through_as_none(self):
        # None (not a hardcoded German string) so render_letter_tex can
        # resolve the *language-appropriate* default itself -- see
        # test_latex.py's TestRenderLetterTex for that resolution.
        with (
            patch("app.pdfgen.render_letter_tex", return_value="") as mock_render,
            patch("app.pdfgen.compile_pdf", return_value=b"%PDF"),
        ):
            r = client.post("/api/generate", json=self._payload())
        assert r.status_code == 200
        assert mock_render.call_args.kwargs["anrede"] is None
        assert mock_render.call_args.kwargs["grussformel"] is None

    def test_explicit_anrede_and_grussformel_are_used(self):
        with (
            patch("app.pdfgen.render_letter_tex", return_value="") as mock_render,
            patch("app.pdfgen.compile_pdf", return_value=b"%PDF"),
        ):
            r = client.post(
                "/api/generate",
                json=self._payload(anrede="Liebe Frau Müller,", grussformel="Beste Grüße"),
            )
        assert r.status_code == 200
        assert mock_render.call_args.kwargs["anrede"] == "Liebe Frau Müller,"
        assert mock_render.call_args.kwargs["grussformel"] == "Beste Grüße"

    def test_empty_anrede_returns_422(self):
        r = client.post("/api/generate", json=self._payload(anrede=""))
        assert r.status_code == 422

    def test_bestellnummer_is_passed_through_when_given(self):
        with (
            patch("app.pdfgen.render_letter_tex", return_value="") as mock_render,
            patch("app.pdfgen.compile_pdf", return_value=b"%PDF"),
        ):
            r = client.post("/api/generate", json=self._payload(bestellnummer="123-4567890"))
        assert r.status_code == 200
        assert mock_render.call_args.kwargs["bestellnummer"] == "123-4567890"

    def test_bestellnummer_defaults_to_none_when_omitted(self):
        with (
            patch("app.pdfgen.render_letter_tex", return_value="") as mock_render,
            patch("app.pdfgen.compile_pdf", return_value=b"%PDF"),
        ):
            r = client.post("/api/generate", json=self._payload())
        assert r.status_code == 200
        assert mock_render.call_args.kwargs["bestellnummer"] is None

    def test_oversized_bestellnummer_returns_422(self):
        r = client.post("/api/generate", json=self._payload(bestellnummer="x" * 101))
        assert r.status_code == 422


class TestCustomSender:
    def _payload(self, **overrides):
        base = {
            "sender_id": "custom",
            "custom_sender": {
                "name": "Max Mustermann",
                "strasse": "Teststr. 5",
                "ort": "99999 Testort",
            },
            "recipient_name": "Empfänger GmbH",
            "recipient_strasse": "Straße 1",
            "recipient_ort": "12345 Stadt",
            "subject": "Betreff",
            "body": "Text.",
        }
        base.update(overrides)
        return base

    def test_custom_sender_is_used(self):
        with (
            patch("app.pdfgen.render_letter_tex", return_value="") as mock_render,
            patch("app.pdfgen.compile_pdf", return_value=b"%PDF"),
        ):
            r = client.post("/api/generate", json=self._payload())
        assert r.status_code == 200
        sender = mock_render.call_args.kwargs["sender"]
        assert sender.name == "Max Mustermann"
        assert sender.strasse == "Teststr. 5"
        assert sender.ort == "99999 Testort"

    def test_custom_sender_missing_object_returns_422(self):
        payload = self._payload()
        del payload["custom_sender"]
        r = client.post("/api/generate", json=payload)
        assert r.status_code == 422

    def test_custom_sender_missing_required_field_returns_422(self):
        payload = self._payload()
        del payload["custom_sender"]["ort"]
        r = client.post("/api/generate", json=payload)
        assert r.status_code == 422

    def test_preset_sender_does_not_need_custom_sender(self):
        with patch("app.pdfgen.compile_pdf", return_value=b"%PDF"):
            r = client.post(
                "/api/generate",
                json=self._payload(sender_id="example", custom_sender=None),
            )
        assert r.status_code == 200


class TestSignature:
    def _payload(self, **overrides):
        base = {
            "sender_id": "example",
            "recipient_name": "Firma GmbH",
            "recipient_strasse": "Musterstraße 1",
            "recipient_ort": "12345 Stadt",
            "subject": "Testbetreff",
            "body": "Testtext.",
        }
        base.update(overrides)
        return base

    def test_valid_signature_is_passed_to_compile_pdf(self):
        with (
            patch("app.pdfgen.render_letter_tex", return_value="") as mock_render,
            patch("app.pdfgen.compile_pdf", return_value=b"%PDF") as mock_compile,
        ):
            r = client.post("/api/generate", json=self._payload(signature=TINY_PNG_B64))

        assert r.status_code == 200
        assert mock_render.call_args.kwargs["has_signature"] is True
        assert mock_compile.call_args.kwargs["signature_png"].startswith(b"\x89PNG")

    def test_data_url_signature_is_accepted(self):
        with (
            patch("app.pdfgen.render_letter_tex", return_value=""),
            patch("app.pdfgen.compile_pdf", return_value=b"%PDF") as mock_compile,
        ):
            r = client.post(
                "/api/generate",
                json=self._payload(signature=f"data:image/png;base64,{TINY_PNG_B64}"),
            )
        assert r.status_code == 200
        assert mock_compile.call_args.kwargs["signature_png"].startswith(b"\x89PNG")

    def test_no_signature_means_no_signature_bytes(self):
        with (
            patch("app.pdfgen.render_letter_tex", return_value="") as mock_render,
            patch("app.pdfgen.compile_pdf", return_value=b"%PDF") as mock_compile,
        ):
            r = client.post("/api/generate", json=self._payload())

        assert r.status_code == 200
        assert mock_render.call_args.kwargs["has_signature"] is False
        assert mock_compile.call_args.kwargs["signature_png"] is None

    def test_invalid_signature_returns_400(self):
        r = client.post("/api/generate", json=self._payload(signature="not-valid-base64!!!"))
        assert r.status_code == 400

    def test_non_png_signature_returns_400(self):
        import base64

        not_a_png = base64.b64encode(b"not actually a png").decode()
        r = client.post("/api/generate", json=self._payload(signature=not_a_png))
        assert r.status_code == 400


class TestUploadToPaperless:
    def _payload(self, **overrides):
        base = {
            "sender_id": "example",
            "recipient_name": "Firma GmbH",
            "recipient_strasse": "Musterstraße 1",
            "recipient_ort": "12345 Stadt",
            "subject": "Testbetreff",
            "body": "Testtext.",
        }
        base.update(overrides)
        return base

    def test_happy_path_returns_task_id(self):
        with (
            patch("app.pdfgen.compile_pdf", return_value=b"%PDF-fake-bytes"),
            patch("app.main.upload_pdf", return_value="task-abc-123") as mock_upload,
        ):
            r = client.post("/api/upload-to-paperless", json=self._payload())

        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "queued"
        assert body["task_id"] == "task-abc-123"
        assert mock_upload.call_args.kwargs["title"] == "Testbetreff"

    def test_not_configured_returns_501(self):
        from app.paperless import PaperlessNotConfigured

        with (
            patch("app.pdfgen.compile_pdf", return_value=b"%PDF"),
            patch("app.main.upload_pdf", side_effect=PaperlessNotConfigured("not configured")),
        ):
            r = client.post("/api/upload-to-paperless", json=self._payload())

        assert r.status_code == 501
        assert "not configured" in r.json()["error"]

    def test_paperless_rejection_returns_502(self):
        from app.paperless import PaperlessUploadError

        with (
            patch("app.pdfgen.compile_pdf", return_value=b"%PDF"),
            patch(
                "app.main.upload_pdf",
                side_effect=PaperlessUploadError("rejected", status_code=403),
            ),
        ):
            r = client.post("/api/upload-to-paperless", json=self._payload())

        assert r.status_code == 502
        body = r.json()
        assert "rejected" in body["error"]
        assert body["paperless_status_code"] == 403

    def test_unknown_sender_returns_400(self):
        r = client.post("/api/upload-to-paperless", json=self._payload(sender_id="nonexistent"))
        assert r.status_code == 400

    def test_compile_error_returns_500_with_log_tail(self):
        with patch(
            "app.pdfgen.compile_pdf",
            side_effect=LatexCompileError("boom", log_tail="! Undefined control sequence."),
        ):
            r = client.post("/api/upload-to-paperless", json=self._payload())

        assert r.status_code == 500
        body = r.json()
        assert "boom" in body["error"]
        assert "Undefined control sequence" in body["log_tail"]


class TestHealthAndStatic:
    def test_healthz(self):
        r = client.get("/healthz")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}

    def test_index_serves_html(self):
        r = client.get("/")
        assert r.status_code == 200
        assert "Brief" in r.text


class TestLetters:
    def _payload(self, **overrides):
        base = {
            "sender_id": "example",
            "recipient_name": "Firma GmbH",
            "recipient_strasse": "Musterstraße 1",
            "recipient_ort": "12345 Stadt",
            "subject": "Testbetreff",
            "body": "Testtext.",
        }
        base.update(overrides)
        return base

    def test_create_and_list_a_letter(self):
        r = client.post("/api/letters", json=self._payload(subject="Kündigung"))
        assert r.status_code == 201
        letter_id = r.json()["id"]

        listed = client.get("/api/letters").json()
        assert any(
            letter["id"] == letter_id and letter["subject"] == "Kündigung" for letter in listed
        )

    def test_get_a_letter_returns_full_fields(self):
        created = client.post("/api/letters", json=self._payload(subject="Widerspruch")).json()
        r = client.get(f"/api/letters/{created['id']}")
        assert r.status_code == 200
        body = r.json()
        assert body["subject"] == "Widerspruch"
        assert body["recipient_name"] == "Firma GmbH"

    def test_get_missing_letter_returns_404(self):
        r = client.get("/api/letters/999999")
        assert r.status_code == 404

    def test_saved_letter_defaults_anrede_and_grussformel_when_omitted(self):
        from app.latex import DEFAULT_ANREDE, DEFAULT_GRUSSFORMEL

        created = client.post("/api/letters", json=self._payload()).json()
        r = client.get(f"/api/letters/{created['id']}")
        assert r.json()["anrede"] == DEFAULT_ANREDE
        assert r.json()["grussformel"] == DEFAULT_GRUSSFORMEL

    def test_custom_anrede_and_grussformel_round_trip_through_update(self):
        created = client.post("/api/letters", json=self._payload()).json()
        r = client.put(
            f"/api/letters/{created['id']}",
            json=self._payload(anrede="Liebe Kolleginnen und Kollegen,", grussformel="Beste Grüße"),
        )
        assert r.status_code == 200
        saved = client.get(f"/api/letters/{created['id']}").json()
        assert saved["anrede"] == "Liebe Kolleginnen und Kollegen,"
        assert saved["grussformel"] == "Beste Grüße"

    def test_bestellnummer_round_trips_through_create_and_update(self):
        created = client.post(
            "/api/letters", json=self._payload(bestellnummer="123-4567890")
        ).json()
        assert client.get(f"/api/letters/{created['id']}").json()["bestellnummer"] == (
            "123-4567890"
        )

        r = client.put(f"/api/letters/{created['id']}", json=self._payload(bestellnummer="999-000"))
        assert r.status_code == 200
        assert client.get(f"/api/letters/{created['id']}").json()["bestellnummer"] == "999-000"

    def test_bestellnummer_defaults_to_none_when_omitted(self):
        created = client.post("/api/letters", json=self._payload()).json()
        assert client.get(f"/api/letters/{created['id']}").json()["bestellnummer"] is None

    def test_updating_without_bestellnummer_clears_it(self):
        # Same "omit -> None" semantics as re-saving without redrawing
        # the signature must NOT accidentally preserve a stale value --
        # unlike the signature (which is deliberately sticky, see
        # _apply_request_to_letter), bestellnummer is a plain optional
        # field with no such special-casing, so omitting it on an
        # update is how a user clears a previously-set one.
        created = client.post(
            "/api/letters", json=self._payload(bestellnummer="123-4567890")
        ).json()
        client.put(f"/api/letters/{created['id']}", json=self._payload())
        assert client.get(f"/api/letters/{created['id']}").json()["bestellnummer"] is None

    def test_update_a_letter(self):
        created = client.post("/api/letters", json=self._payload(subject="Alt")).json()
        r = client.put(f"/api/letters/{created['id']}", json=self._payload(subject="Neu"))
        assert r.status_code == 200
        assert r.json()["subject"] == "Neu"
        assert client.get(f"/api/letters/{created['id']}").json()["subject"] == "Neu"

    def test_delete_a_letter(self):
        created = client.post("/api/letters", json=self._payload()).json()
        r = client.delete(f"/api/letters/{created['id']}")
        assert r.status_code == 204
        assert client.get(f"/api/letters/{created['id']}").status_code == 404

    def test_letter_pdf_regenerates_from_saved_fields(self):
        created = client.post("/api/letters", json=self._payload(subject="PDF-Test")).json()
        with patch("app.pdfgen.compile_pdf", return_value=b"%PDF-fake-bytes"):
            r = client.get(f"/api/letters/{created['id']}/pdf")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"
        assert r.content == b"%PDF-fake-bytes"

    def test_saved_signature_round_trips_through_update(self):
        created = client.post(
            "/api/letters", json=self._payload(signature=f"data:image/png;base64,{TINY_PNG_B64}")
        ).json()
        fetched = client.get(f"/api/letters/{created['id']}").json()
        assert fetched["signature"].startswith("data:image/png;base64,")

        # Re-saving without touching the signature pad must not drop it.
        client.put(f"/api/letters/{created['id']}", json=self._payload())
        fetched_again = client.get(f"/api/letters/{created['id']}").json()
        assert fetched_again["signature"].startswith("data:image/png;base64,")

    def test_custom_sender_round_trips(self):
        payload = self._payload(
            sender_id="custom",
            custom_sender={
                "name": "Max Mustermann",
                "strasse": "Teststr. 5",
                "ort": "99999 Testort",
            },
        )
        created = client.post("/api/letters", json=payload).json()
        fetched = client.get(f"/api/letters/{created['id']}").json()
        assert fetched["custom_sender"]["name"] == "Max Mustermann"
