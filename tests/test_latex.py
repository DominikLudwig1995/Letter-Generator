"""Tests for LaTeX escaping, template rendering, and PDF compilation."""

import shutil
import subprocess
from datetime import date
from unittest.mock import patch

import pytest

from app.latex import (
    LatexCompileError,
    compile_pdf,
    escape_latex,
    escape_latex_lines,
    escape_latex_paragraphs,
    format_german_date,
    render_letter_tex,
)
from app.senders import get_sender

pdflatex_available = shutil.which("pdflatex") is not None


class TestEscapeLatex:
    @pytest.mark.parametrize(
        "raw,expected_substring",
        [
            ("100% sicher", r"100\% sicher"),
            ("A & B", r"A \& B"),
            ("#1 Kunde", r"\#1 Kunde"),
            ("$100", r"\$100"),
            ("a_b", r"a\_b"),
            ("{braces}", r"\{braces\}"),
        ],
    )
    def test_escapes_special_characters(self, raw, expected_substring):
        assert expected_substring in escape_latex(raw)

    def test_backslash_is_escaped_and_not_double_escaped(self):
        result = escape_latex(r"\input{/etc/passwd}")
        # The literal backslash must never survive as a raw LaTeX command
        # character -- it becomes \textbackslash{}, and the braces that
        # followed it are separately escaped, not reinterpreted as a
        # LaTeX group.
        assert r"\input" not in result or r"\textbackslash{}input" in result
        assert "\\{" in result and "\\}" in result

    def test_plain_text_is_unchanged(self):
        assert escape_latex("Hello World 123") == "Hello World 123"

    def test_ascii_hyphen_is_unchanged(self):
        assert escape_latex("Test-Betreff mit Bindestrich") == "Test-Betreff mit Bindestrich"

    @pytest.mark.parametrize(
        "unicode_dash",
        [
            "−",  # − MINUS SIGN -- crashes pdflatex outright, see TestCompilePdf below
            "‐",  # ‐ HYPHEN
            "‑",  # ‑ NON-BREAKING HYPHEN
            "‒",  # ‒ FIGURE DASH
            "–",  # – EN DASH
            "—",  # — EM DASH
            "―",  # ― HORIZONTAL BAR
            "﹣",  # ﹣ SMALL HYPHEN-MINUS
            "－",  # － FULLWIDTH HYPHEN-MINUS
            "­",  # SOFT HYPHEN -- invisible; would otherwise survive as
            # a zero-width character no one can see and no one typed
        ],
    )
    def test_unicode_dash_lookalikes_are_normalized_to_ascii_hyphen(self, unicode_dash):
        result = escape_latex(f"a{unicode_dash}b")
        assert result == "a-b"
        assert unicode_dash not in result

    def test_unrelated_symbols_are_not_treated_as_dashes(self):
        # Guards the switch to a Unicode-category-based check: math/other
        # symbols that merely *look* line-like must not get swept up.
        assert escape_latex("a=b") == "a=b"
        assert escape_latex("a|b") == "a|b"
        assert escape_latex("a_b") == r"a\_b"


class TestEscapeLatexLines:
    def test_joins_lines_with_latex_linebreak(self):
        result = escape_latex_lines("Firma GmbH\nMusterstraße 1\n12345 Stadt")
        assert result == "Firma GmbH \\\\\nMusterstraße 1 \\\\\n12345 Stadt"

    def test_drops_blank_lines(self):
        result = escape_latex_lines("Line1\n\n\nLine2")
        assert result == "Line1 \\\\\nLine2"

    def test_escapes_each_line(self):
        result = escape_latex_lines("A & B\nC % D")
        assert r"A \& B" in result
        assert r"C \% D" in result


class TestEscapeLatexParagraphs:
    def test_blank_line_separates_paragraphs(self):
        result = escape_latex_paragraphs("Erster Absatz.\n\nZweiter Absatz.")
        assert "\n\n" in result
        paragraphs = result.split("\n\n")
        assert len(paragraphs) == 2
        assert "Erster Absatz." in paragraphs[0]
        assert "Zweiter Absatz." in paragraphs[1]

    def test_single_newline_within_paragraph_becomes_soft_break(self):
        result = escape_latex_paragraphs("Zeile 1\nZeile 2")
        assert "\\\\\n" in result
        assert "\n\n" not in result

    def test_escapes_content(self):
        result = escape_latex_paragraphs("Kosten: 50% & mehr")
        assert r"\%" in result
        assert r"\&" in result

    def test_single_star_becomes_bold(self):
        result = escape_latex_paragraphs("Das ist *wichtig* für Sie.")
        assert r"\textbf{wichtig}" in result
        assert "*" not in result

    def test_double_star_becomes_italic(self):
        result = escape_latex_paragraphs("Das ist **sehr wichtig** für Sie.")
        assert r"\textit{sehr wichtig}" in result
        assert "*" not in result

    def test_bold_and_italic_together(self):
        result = escape_latex_paragraphs("*fett* und **kursiv** im selben Satz.")
        assert r"\textbf{fett}" in result
        assert r"\textit{kursiv}" in result

    def test_markup_content_is_still_escaped(self):
        result = escape_latex_paragraphs("*100% sicher*")
        assert r"\textbf{100\% sicher}" in result

    def test_unmatched_single_star_is_left_literal(self):
        # No closing '*' -- must not crash or silently drop the star,
        # just fall through as ordinary (escaped) text.
        result = escape_latex_paragraphs("Preis: 5*3=15")
        assert r"\textbf" not in result
        assert "5*3=15" in result

    def test_markup_does_not_cross_paragraph_boundary(self):
        result = escape_latex_paragraphs("*offen\n\nneuer Absatz*")
        assert r"\textbf" not in result

    def test_markup_does_not_cross_manual_line_break(self):
        # Same paragraph, but a single newline (soft break, not a new
        # paragraph) -- matching is per-line, so this must not merge
        # across the \\ break either. See the docstring on why.
        result = escape_latex_paragraphs("*offen\nweiter*")
        assert r"\textbf" not in result


class TestFormatGermanDate:
    def test_formats_with_german_month_name(self):
        assert format_german_date(date(2026, 8, 13)) == "13. August 2026"

    def test_january(self):
        assert format_german_date(date(2026, 1, 1)) == "1. Januar 2026"

    def test_december(self):
        assert format_german_date(date(2026, 12, 31)) == "31. Dezember 2026"


class TestRenderLetterTex:
    def test_renders_all_fields(self):
        sender = get_sender("example")
        tex = render_letter_tex(
            sender=sender,
            recipient_name="Firma GmbH",
            recipient_strasse="Musterstraße 1",
            recipient_ort="12345 Stadt",
            subject="Test-Betreff",
            body="Dies ist ein Testtext.",
            letter_date=date(2026, 8, 13),
        )
        assert "Jane Doe" in tex
        assert "Main Str" in tex
        assert "Test-Betreff" in tex
        assert "Dies ist ein Testtext." in tex
        assert "Firma GmbH" in tex
        assert "13. August 2026" in tex

    def test_recipient_and_body_are_escaped_in_output(self):
        sender = get_sender("example")
        tex = render_letter_tex(
            sender=sender,
            recipient_name="Firma & Co",
            recipient_strasse="",
            recipient_ort="",
            subject="100% Test",
            body="A_B $C$",
            letter_date=date(2026, 8, 13),
        )
        assert r"Firma \& Co" in tex
        assert r"100\% Test" in tex
        assert r"A\_B \$C\$" in tex

    def test_uses_g_brief_document_class(self):
        sender = get_sender("example")
        tex = render_letter_tex(
            sender=sender,
            recipient_name="X",
            recipient_strasse="",
            recipient_ort="",
            subject="Y",
            body="Z",
            letter_date=date(2026, 8, 13),
        )
        assert r"\documentclass[10pt,ngerman]{g-brief}" in tex
        assert r"\begin{g-brief}" in tex
        assert r"\end{g-brief}" in tex

    def test_english_language_switches_document_class_and_babel(self):
        sender = get_sender("example")
        tex = render_letter_tex(
            sender=sender,
            recipient_name="X",
            recipient_strasse="",
            recipient_ort="",
            subject="Y",
            body="Z",
            letter_date=date(2026, 8, 13),
            language="en",
        )
        assert r"\documentclass[10pt,english]{g-brief}" in tex
        assert r"\usepackage[english]{babel}" in tex
        assert r"\usepackage{ngerman}" not in tex

    def test_english_language_formats_date_in_english(self):
        sender = get_sender("example")
        tex = render_letter_tex(
            sender=sender,
            recipient_name="X",
            recipient_strasse="",
            recipient_ort="",
            subject="Y",
            body="Z",
            letter_date=date(2026, 8, 13),
            language="en",
        )
        assert "August 13, 2026" in tex
        assert "13. August 2026" not in tex

    def test_english_language_defaults_anrede_and_grussformel_in_english(self):
        sender = get_sender("example")
        tex = render_letter_tex(
            sender=sender,
            recipient_name="X",
            recipient_strasse="",
            recipient_ort="",
            subject="Y",
            body="Z",
            letter_date=date(2026, 8, 13),
            language="en",
        )
        assert "Dear Sir or Madam" in tex
        assert "Kind regards" in tex
        assert "Sehr geehrte Damen und Herren" not in tex

    def test_explicit_anrede_overrides_language_default_even_in_english(self):
        sender = get_sender("example")
        tex = render_letter_tex(
            sender=sender,
            recipient_name="X",
            recipient_strasse="",
            recipient_ort="",
            subject="Y",
            body="Z",
            letter_date=date(2026, 8, 13),
            language="en",
            anrede="Dear Ms. Doe,",
        )
        assert "Dear Ms. Doe," in tex
        assert "Dear Sir or Madam" not in tex

    def test_bestellnummer_label_is_translated_in_english(self):
        sender = get_sender("example")
        tex = render_letter_tex(
            sender=sender,
            recipient_name="X",
            recipient_strasse="",
            recipient_ort="",
            subject="Y",
            body="Z",
            letter_date=date(2026, 8, 13),
            language="en",
            bestellnummer="ABC-123",
        )
        assert "Order/Customer number: ABC-123" in tex
        assert "Bestellnummer" not in tex

    def test_custom_sender_fields_are_used(self):
        from app.senders import Sender

        custom = Sender(
            id="custom", name="Max Mustermann", strasse="Teststr. 5", ort="99999 Testort"
        )
        tex = render_letter_tex(
            sender=custom,
            recipient_name="X",
            recipient_strasse="",
            recipient_ort="",
            subject="Y",
            body="Z",
            letter_date=date(2026, 8, 13),
        )
        assert "Max Mustermann" in tex
        assert "Teststr. 5" in tex
        assert "99999 Testort" in tex

    def test_custom_date_is_used_instead_of_today(self):
        sender = get_sender("example")
        tex = render_letter_tex(
            sender=sender,
            recipient_name="X",
            recipient_strasse="",
            recipient_ort="",
            subject="Y",
            body="Z",
            letter_date=date(2020, 1, 1),
        )
        assert "1. Januar 2020" in tex
        assert r"\today" not in tex

    def test_anrede_and_grussformel_default_when_omitted(self):
        sender = get_sender("example")
        tex = render_letter_tex(
            sender=sender,
            recipient_name="X",
            recipient_strasse="",
            recipient_ort="",
            subject="Y",
            body="Z",
            letter_date=date(2026, 8, 13),
        )
        assert r"\Anrede              {Sehr geehrte Damen und Herren,}" in tex
        assert "Mit freundlichen Gr" in tex  # ß may render differently by encoding path

    def test_anrede_and_grussformel_are_used_when_given(self):
        sender = get_sender("example")
        tex = render_letter_tex(
            sender=sender,
            recipient_name="X",
            recipient_strasse="",
            recipient_ort="",
            subject="Y",
            body="Z",
            letter_date=date(2026, 8, 13),
            anrede="Liebe Frau Müller,",
            grussformel="Beste Grüße",
        )
        assert "Liebe Frau M" in tex
        assert "Beste Gr" in tex
        assert "Sehr geehrte Damen und Herren" not in tex
        assert "Mit freundlichen Gr" not in tex

    def test_bestellnummer_renders_as_a_line_above_the_subject_when_given(self):
        sender = get_sender("example")
        tex = render_letter_tex(
            sender=sender,
            recipient_name="X",
            recipient_strasse="",
            recipient_ort="",
            subject="Y",
            body="Z",
            letter_date=date(2026, 8, 13),
            bestellnummer="123-4567890",
        )
        assert "Bestellnummer/Kundennummer: 123-4567890" in tex
        # Not g-brief's own reference-line field -- see render_letter_tex's
        # docstring for why (it can't show just one of its three columns).
        assert r"\IhrZeichen" not in tex

    def test_bestellnummer_omitted_when_not_given(self):
        sender = get_sender("example")
        tex = render_letter_tex(
            sender=sender,
            recipient_name="X",
            recipient_strasse="",
            recipient_ort="",
            subject="Y",
            body="Z",
            letter_date=date(2026, 8, 13),
        )
        assert "Bestellnummer/Kundennummer" not in tex

    def test_bestellnummer_omitted_when_blank(self):
        # A whitespace-only value from the frontend must behave like
        # "not given" -- same treatment as a genuinely absent field, not
        # an empty "Bestellnummer/Kundennummer: " line.
        sender = get_sender("example")
        tex = render_letter_tex(
            sender=sender,
            recipient_name="X",
            recipient_strasse="",
            recipient_ort="",
            subject="Y",
            body="Z",
            letter_date=date(2026, 8, 13),
            bestellnummer="   ",
        )
        assert "Bestellnummer/Kundennummer" not in tex

    def test_bestellnummer_is_escaped(self):
        sender = get_sender("example")
        tex = render_letter_tex(
            sender=sender,
            recipient_name="X",
            recipient_strasse="",
            recipient_ort="",
            subject="Y",
            body="Z",
            letter_date=date(2026, 8, 13),
            bestellnummer="Nr. 100% & Co",
        )
        assert r"100\% \& Co" in tex


@pytest.mark.skipif(not pdflatex_available, reason="pdflatex not installed")
class TestCompilePdf:
    def test_compiles_a_valid_letter_to_pdf_bytes(self):
        sender = get_sender("example")
        tex = render_letter_tex(
            sender=sender,
            recipient_name="Test GmbH",
            recipient_strasse="Musterstraße 1",
            recipient_ort="12345 Musterstadt",
            subject="Testbrief",
            body="Dies ist ein Testtext für den Kompilierungstest.",
            letter_date=date(2026, 8, 13),
        )
        pdf_bytes = compile_pdf(tex)
        assert pdf_bytes.startswith(b"%PDF")
        assert len(pdf_bytes) > 1000

    def test_raises_on_broken_tex(self):
        with pytest.raises(LatexCompileError):
            compile_pdf(r"\documentclass{article}\begin{document}\unknowncommand{x}\end{document}")

    def test_non_utf8_pdflatex_output_raises_latex_compile_error_not_unicode_error(self):
        """Regression test: pdflatex's own stdout/stderr isn't reliably
        UTF-8 (TeX Live's German hyphenation-pattern files are Latin-1,
        and pdflatex echoes lines from them verbatim). This used to be
        decoded with text=True's strict UTF-8 codec, so a Latin-1 byte
        anywhere in pdflatex's own output crashed with an unhandled
        UnicodeDecodeError -- a bare 500 with no JSON body at all,
        regardless of whether the letter's content was the problem."""
        fake_result = subprocess.CompletedProcess(
            args=["pdflatex"],
            returncode=1,
            stdout=b"Loading hyphenation patterns f\xfcr deutsche Sprache...",
            stderr=b"",
        )
        with (
            patch("app.latex.subprocess.run", return_value=fake_result),
            pytest.raises(LatexCompileError) as exc_info,
        ):
            compile_pdf(r"\documentclass{article}\begin{document}x\end{document}")
        assert "f" in exc_info.value.log_tail  # decoded without raising

    def test_compiles_with_a_real_signature_image(self):
        """End-to-end check of the has_signature template path against
        real pdflatex, not just the mocked API-level tests -- this is new
        LaTeX logic (\\includegraphics inside \\Unterschrift), worth
        proving actually compiles rather than trusting the template
        syntax by inspection."""
        import base64

        tiny_png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
            "+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        sender = get_sender("example")
        tex = render_letter_tex(
            sender=sender,
            recipient_name="Test GmbH",
            recipient_strasse="Musterstraße 1",
            recipient_ort="12345 Musterstadt",
            subject="Testbrief mit Unterschrift",
            body="Dies ist ein Testtext.",
            letter_date=date(2026, 8, 13),
            has_signature=True,
        )
        pdf_bytes = compile_pdf(tex, signature_png=tiny_png)
        assert pdf_bytes.startswith(b"%PDF")
        assert len(pdf_bytes) > 1000

    def test_compiles_with_unicode_minus_sign_in_body(self):
        """Regression test: a bare U+2212 MINUS SIGN (what some phone
        keyboards/autocorrect substitute for a typed "-") used to crash
        pdflatex outright with 'Unicode character not set up for use
        with LaTeX' since that glyph isn't in the g-brief template's
        fonts. escape_latex now normalizes it (and its lookalikes) to a
        plain ASCII hyphen before the .tex source is ever built."""
        sender = get_sender("example")
        tex = render_letter_tex(
            sender=sender,
            recipient_name="Test GmbH",
            recipient_strasse="Musterstraße 1",
            recipient_ort="12345 Musterstadt",
            subject="Test − Betreff",
            body="Text mit einem − Zeichen (echtes U+2212 MINUS SIGN).",
            letter_date=date(2026, 8, 13),
        )
        pdf_bytes = compile_pdf(tex)
        assert pdf_bytes.startswith(b"%PDF")
        assert len(pdf_bytes) > 1000

    def test_compiles_with_bestellnummer_and_inline_markup(self):
        """End-to-end check against real pdflatex for both new pieces
        together: the bestellnummer line prepended into \\Betreff and the
        *fett*/**kursiv** -> \\textbf{}/\\textit{} body markup -- proving
        the actual .tex syntax compiles, not just that the right
        strings appear in the rendered source (see the mocked-level
        tests in TestRenderLetterTex and TestEscapeLatexParagraphs)."""
        sender = get_sender("example")
        tex = render_letter_tex(
            sender=sender,
            recipient_name="Test GmbH",
            recipient_strasse="Musterstraße 1",
            recipient_ort="12345 Musterstadt",
            subject="Testbrief mit Bestellnummer",
            body="Dies ist *fett* und **kursiv** im selben Satz.",
            letter_date=date(2026, 8, 13),
            bestellnummer="ABC-123456",
        )
        pdf_bytes = compile_pdf(tex)
        assert pdf_bytes.startswith(b"%PDF")
        assert len(pdf_bytes) > 1000
