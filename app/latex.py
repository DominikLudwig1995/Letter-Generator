"""LaTeX escaping, template rendering, and PDF compilation.

Every user-supplied field (recipient block, subject, body) gets interpolated
into a raw .tex source file that's then handed to pdflatex. Two separate
safety properties matter here and are easy to conflate:

1. Escaping -- user text must never be interpreted as LaTeX syntax (a
   recipient field containing "\\input{/etc/passwd}" or a stray "}" that
   closes a brace early must render as literal text, not execute/break the
   document). Handled by escape_latex() below, applied to every field.
2. No arbitrary code execution -- pdflatex's \\write18 (shell-escape) is
   never enabled (no -shell-escape flag, ever), so even a successful
   escape bypass couldn't run shell commands. This is the actual hard
   security boundary; escaping is defense in depth on top of it, not a
   substitute for it.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import unicodedata
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.senders import Sender

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"

# Month names for date formatting -- deliberately not relying on the
# server's locale (setlocale is process-global, environment-dependent,
# and a container image is not guaranteed to have de_DE generated),
# since a letter with the wrong-language date is exactly the kind of
# silent-until-someone-notices bug a hardcoded table avoids.
_MONTHS_DE = [
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
]  # fmt: skip
_MONTHS_EN = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]  # fmt: skip

SUPPORTED_LANGUAGES = ("de", "en")


def format_date(d: date, language: str = "de") -> str:
    """German: '13. August 2026' (matching \\today under \\usepackage{ngerman}).
    English: 'August 13, 2026' (US-style, matching g-brief's own
    [english] class option's date convention)."""
    if language == "en":
        return f"{_MONTHS_EN[d.month - 1]} {d.day}, {d.year}"
    return f"{d.day}. {_MONTHS_DE[d.month - 1]} {d.year}"


def format_german_date(d: date) -> str:
    """Deprecated alias for format_date(d, 'de') -- kept for backwards
    compatibility with existing callers."""
    return format_date(d, "de")


# Custom delimiters: the template's own content is LaTeX, which uses "{"
# and "}" constantly -- Jinja2's default {{ }}/{% %} would be confusing to
# read and a source of accidental collisions. \VAR{}/\BLOCK{} is the
# established pattern for Jinja2-templated LaTeX for exactly this reason.
_env = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    variable_start_string=r"\VAR{",
    variable_end_string="}",
    block_start_string=r"\BLOCK{",
    block_end_string="}",
    comment_start_string=r"\#{",
    comment_end_string="}",
    trim_blocks=True,
    lstrip_blocks=True,
    autoescape=False,  # this is LaTeX escaping, not HTML -- see escape_latex()
)

# Order matters: backslash must be escaped first, since every other
# replacement below introduces new backslashes that must not themselves
# be re-escaped on a later pass.
_LATEX_ESCAPE_MAP = [
    ("\\", r"\textbackslash{}"),
    ("{", r"\{"),
    ("}", r"\}"),
    ("$", r"\$"),
    ("&", r"\&"),
    ("#", r"\#"),
    ("^", r"\textasciicircum{}"),
    ("_", r"\_"),
    ("%", r"\%"),
    ("~", r"\textasciitilde{}"),
]


# Unicode lookalikes of a plain ASCII hyphen-minus that phone/browser
# "smart punctuation" autocorrect (and copy-pasting from spreadsheets,
# calculators, or word processors) routinely substitutes for a typed
# "-". Most of these render fine as-is (en/em dash are in the font),
# but U+2212 MINUS SIGN is a math-only glyph the g-brief template's
# fonts (T1/EC via cm-super, no math font loaded) have no mapping for
# at all -- pdflatex fails hard with "Unicode character not set up for
# use with LaTeX", not a garbled character.
#
# Rather than maintaining a hand-picked list of lookalikes (which keeps
# missing whatever the next keyboard/app substitutes), this normalizes
# every character in Unicode's own "Dash Punctuation" category (Pd --
# covers hyphen, non-breaking hyphen, figure/en/em dash, horizontal
# bar, and every other dash a Unicode-aware input method could produce)
# plus the two dash-shaped characters outside that category that are
# known to actually break this pipeline: MINUS SIGN (a math symbol,
# category Sm) and SOFT HYPHEN (an invisible line-break hint, category
# Cf, that would otherwise survive as a zero-width character no one
# can see and no one typed on purpose).
def _is_dash_lookalike(char: str) -> bool:
    return unicodedata.category(char) == "Pd" or char in ("−", "­")


def escape_latex(text: str) -> str:
    """Escape a plain string so it renders as literal text in LaTeX."""
    text = "".join("-" if _is_dash_lookalike(c) else c for c in text)
    for char, replacement in _LATEX_ESCAPE_MAP:
        text = text.replace(char, replacement)
    return text


# Inline markup in the letter body: *fett* -> \textbf{}, **kursiv** ->
# \textit{}. The double-star alternative is listed first so "**word**"
# is recognized whole as italic rather than as two adjacent (and
# invalid, since there's nothing between the second and third star)
# bold markers -- Python's re tries alternatives left-to-right at each
# position, so ordering here is load-bearing, not stylistic. Matched
# before escaping (the * markers are plain ASCII, not LaTeX-special)
# so only the text *between* markers goes through the usual escaping.
_MARKUP_PATTERN = re.compile(r"\*\*(.+?)\*\*|\*(.+?)\*", re.DOTALL)


def _escape_with_markup(text: str) -> str:
    r"""escape_latex, plus *fett* -> \textbf{} and **kursiv** ->
    \textit{}. Unmatched/odd '*' (no closing marker) is left as literal
    escaped text -- the regex simply doesn't match it, so it falls
    through in the plain-text spans between matches unchanged."""
    parts = []
    pos = 0
    for m in _MARKUP_PATTERN.finditer(text):
        parts.append(escape_latex(text[pos : m.start()]))
        italic, bold = m.group(1), m.group(2)
        if italic is not None:
            parts.append(f"\\textit{{{escape_latex(italic)}}}")
        else:
            parts.append(f"\\textbf{{{escape_latex(bold)}}}")
        pos = m.end()
    parts.append(escape_latex(text[pos:]))
    return "".join(parts)


def escape_latex_lines(text: str) -> str:
    r"""Escape a multi-line block (e.g. a recipient address) and join
    lines with LaTeX's explicit line break (\\), matching how the g-brief
    template's own \Adresse field is hand-written (one physical line per
    address line, joined with \\)."""
    lines = [escape_latex(line.strip()) for line in text.splitlines() if line.strip()]
    return " \\\\\n".join(lines)


def escape_latex_paragraphs(text: str) -> str:
    """Escape a multi-paragraph body. A blank line starts a new LaTeX
    paragraph naturally, so paragraphs are preserved as blank-line-
    separated blocks; single line breaks within a paragraph become \\
    (a soft break, not a new paragraph). Per-line rather than
    per-paragraph markup matching -- *fett*/**kursiv** spanning a \\
    line break inside \textbf{}/\textit{} would read oddly in the
    output, and nothing about the letter-writing use case needs
    emphasis to cross a line the author manually broke."""
    # Normalize line endings and split on blank lines.
    normalized = re.sub(r"\r\n?", "\n", text.strip())
    paragraphs = re.split(r"\n\s*\n", normalized)
    rendered_paragraphs = []
    for para in paragraphs:
        lines = [_escape_with_markup(line.strip()) for line in para.splitlines() if line.strip()]
        rendered_paragraphs.append(" \\\\\n".join(lines))
    return "\n\n".join(rendered_paragraphs)


# Filename the signature image is always written under inside the
# per-request compile tempdir (see compile_pdf) -- fixed, not user-
# controlled, so the template can \includegraphics it by a constant
# name regardless of what the client originally uploaded it as.
SIGNATURE_FILENAME = "signature.png"

# Matches the text that used to be hardcoded directly in
# templates/letter.tex.j2 -- kept here as the single source of truth for
# "what a letter looks like if the caller doesn't specify one", so
# existing callers (and the frontend, which mirrors these as its form
# defaults) see byte-identical output to before Anrede/Gruss became
# configurable.
DEFAULT_ANREDE = "Sehr geehrte Damen und Herren,"
DEFAULT_GRUSSFORMEL = "Mit freundlichen Grüßen"
DEFAULT_ANREDE_EN = "Dear Sir or Madam,"
DEFAULT_GRUSSFORMEL_EN = "Kind regards"


def default_anrede(language: str = "de") -> str:
    return DEFAULT_ANREDE_EN if language == "en" else DEFAULT_ANREDE


def default_grussformel(language: str = "de") -> str:
    return DEFAULT_GRUSSFORMEL_EN if language == "en" else DEFAULT_GRUSSFORMEL


def render_letter_tex(
    sender: Sender,
    recipient_name: str,
    recipient_strasse: str,
    recipient_ort: str,
    subject: str,
    body: str,
    letter_date: date,
    has_signature: bool = False,
    anrede: str | None = None,
    grussformel: str | None = None,
    bestellnummer: str | None = None,
    language: str = "de",
) -> str:
    """Render the g-brief .tex source for one letter. Every user/sender-
    derived field is escaped before reaching the template -- the template
    itself only ever inserts already-escaped text.

    recipient_name/_strasse/_ort are three separate fields (matching the
    sender's own name/strasse/ort shape) rather than one freeform
    address block -- recipient_name may itself be multiple lines (e.g. a
    company name plus a department line), strasse and ort are normally
    single lines each, but all three go through the same line-escaping
    either way.

    has_signature only controls whether the template emits an
    \\includegraphics for SIGNATURE_FILENAME -- the actual image bytes
    are handled by compile_pdf, which is the thing that actually needs
    to write signature.png into the same tempdir as the .tex source
    before pdflatex runs.

    bestellnummer, if given, is prepended as its own line inside
    \\Betreff itself ("Bestellnummer/Kundennummer: ..." above the
    subject, in the same bold block) rather than g-brief's built-in
    \\IhrZeichen reference-line field -- that field's macro always
    prints all three of its column headers ("Ihr Zeichen" / "Ihr
    Schreiben vom" / "Mein Zeichen") together whenever any one of them
    is set (g-brief.cls has no way to show only one), which is far more
    than a simple order/customer number needs. Optional: omitted
    entirely (both here and in the template) when not given, same as
    before this field existed.

    language selects both the document class option ([ngerman] vs
    [english] -- g-brief's own built-in translations for every fixed
    label: "Betr." vs "Subj.", etc.) and this function's own date
    formatting/anrede-grussformel defaults. anrede/grussformel left as
    None pick up the language-appropriate default (default_anrede/
    default_grussformel below) rather than always falling back to
    German regardless of language, which is why they're no longer
    plain string defaults on the signature itself.
    """
    template = _env.get_template("letter.tex.j2")
    recipient_block = "\n".join(
        part for part in (recipient_name, recipient_strasse, recipient_ort) if part.strip()
    )
    resolved_anrede = anrede if anrede is not None else default_anrede(language)
    resolved_grussformel = grussformel if grussformel is not None else default_grussformel(language)
    return template.render(
        sender_name=escape_latex(sender.name),
        sender_strasse=escape_latex(sender.strasse),
        sender_zusatz=escape_latex(sender.zusatz),
        sender_ort=escape_latex(sender.ort),
        sender_land=escape_latex(sender.land),
        sender_email=escape_latex(sender.email),
        recipient=escape_latex_lines(recipient_block),
        subject=escape_latex(subject),
        body=escape_latex_paragraphs(body),
        date=escape_latex(format_date(letter_date, language)),
        has_signature=has_signature,
        signature_filename=SIGNATURE_FILENAME,
        anrede=escape_latex(resolved_anrede),
        grussformel=escape_latex(resolved_grussformel),
        bestellnummer=escape_latex(bestellnummer)
        if bestellnummer and bestellnummer.strip()
        else None,
        bestellnummer_label="Order/Customer number"
        if language == "en"
        else "Bestellnummer/Kundennummer",
        doc_class_language="english" if language == "en" else "ngerman",
        babel_language="english" if language == "en" else "ngerman",
    )


class LatexCompileError(RuntimeError):
    """pdflatex failed. `log_tail` carries the last portion of its own
    log output, which is far more useful for diagnosing a bad template
    change than the bare non-zero exit code."""

    def __init__(self, message: str, log_tail: str = "") -> None:
        super().__init__(message)
        self.log_tail = log_tail


def compile_pdf(
    tex_source: str, signature_png: bytes | None = None, timeout_seconds: int = 30
) -> bytes:
    """Compile a .tex source string to PDF bytes via pdflatex.

    Runs in an isolated temp directory, non-interactively, with
    shell-escape never enabled (see this module's docstring) -- pdflatex
    itself cannot execute arbitrary commands regardless of what ends up
    in the .tex source. Compiled twice: g-brief resolves some layout
    details (e.g. folding marks) on a second pass, matching normal LaTeX
    practice for any document class beyond the most trivial.

    signature_png, if given, is written into the same tempdir as the
    .tex source under SIGNATURE_FILENAME before compiling -- the caller
    (app/main.py) is responsible for having already validated it's a
    real PNG and for the template having been rendered with
    has_signature=True, or pdflatex will fail on a missing
    \\includegraphics target.
    """
    with tempfile.TemporaryDirectory(prefix="brief-") as tmpdir:
        tex_path = Path(tmpdir) / "letter.tex"
        tex_path.write_text(tex_source, encoding="utf-8")

        if signature_png is not None:
            (Path(tmpdir) / SIGNATURE_FILENAME).write_bytes(signature_png)

        result = None
        for _ in range(2):
            result = subprocess.run(
                [
                    "pdflatex",
                    "-interaction=nonstopmode",
                    "-halt-on-error",
                    "-no-shell-escape",
                    "-output-directory",
                    tmpdir,
                    str(tex_path),
                ],
                capture_output=True,
                # Deliberately not text=True: pdflatex's own terminal
                # output isn't reliably UTF-8. Some of TeX Live's German
                # hyphenation-pattern files (loaded transitively via
                # \usepackage{ngerman}) are themselves Latin-1, and
                # pdflatex echoes lines from them (and from overfull-hbox
                # warnings quoting the paragraph text verbatim) as raw
                # bytes without re-encoding. text=True's strict UTF-8
                # decode used to blow up on that with an unhandled
                # UnicodeDecodeError -- a bare 500 with no JSON body at
                # all, not even a LatexCompileError, however innocuous
                # the actual letter content was. Decoding ourselves with
                # errors="replace" survives that either way.
                timeout=timeout_seconds,
                cwd=tmpdir,
            )
            if result.returncode != 0:
                break

        pdf_path = Path(tmpdir) / "letter.pdf"
        if result is None or result.returncode != 0 or not pdf_path.exists():
            log = ""
            if result is not None:
                log = _decode_latex_output(result.stdout) + _decode_latex_output(result.stderr)
            raise LatexCompileError("pdflatex failed to produce a PDF", log_tail=log[-4000:])

        return pdf_path.read_bytes()


def _decode_latex_output(raw: bytes) -> str:
    """See the comment in compile_pdf -- pdflatex's own stdout/stderr
    isn't reliably UTF-8, so this never raises on malformed bytes;
    anything undecodable is simply not going to be legible in the log
    tail anyway."""
    return raw.decode("utf-8", errors="replace")
