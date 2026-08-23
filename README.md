# Letter Generator

**Self-hosted business letter generator that renders real LaTeX PDFs — no template lock-in, no SaaS.**

Fill in a recipient, subject and text, preview the PDF, download it. Sender
address, salutation, closing and date are sensible defaults so the form
only ever asks for what actually changes between letters. Uses the
[g-brief](https://ctan.org/pkg/g-brief) LaTeX document class — a standard
German business-letter layout, folding/hole-punch marks included — compiled
via `pdflatex`.

## Features

- ✉️ **Standard letter layout** via the `g-brief` LaTeX class — proper
  German business-letter formatting, not a browser-print hack.
- 🌍 **Bilingual PDFs** — generate the letter in German or English per
  letter; every fixed label (date format, "Subj." vs "Betr.", salutation/
  closing defaults) switches with it.
- ✍️ **Inline text markup** — `*bold*` and `**italic**` in the letter body
  render as real LaTeX emphasis in the PDF.
- 🔢 **Optional order/customer number** — rendered as its own line above
  the subject when given.
- ✒️ **Signature pad** — draw a signature on-screen; it's embedded in the
  PDF, or falls back to just the typed name.
- 💾 **Save and edit letters later** — the sidebar lists every saved
  letter; picking one loads its fields back into the form.
- 👤 **Configurable sender profiles** — quick-pick presets for the
  addresses you send from most often, plus a freeform one-off sender for
  everything else.
- 🔒 **Your data stays yours** — everything lives in a local SQLite
  database plus saved signatures under `data/` (`BRIEF_DATA_DIR`). No
  external service involved.

## Quick start (Docker)

No local Python or LaTeX setup required — one command:

```bash
docker compose up --build
```

Open `http://localhost:8000`. Data (SQLite database, signatures) persists
across restarts in the `brief-data` volume.

## Configuring your own sender

Out of the box, the app ships with one generic example sender ("Jane Doe").
Configure your own via the `BRIEF_SENDERS_JSON` environment variable — a
JSON object mapping an id to `{name, strasse, ort, zusatz, land, email}`:

```bash
BRIEF_SENDERS_JSON='{"me": {"name": "Jane Doe", "strasse": "Main St 1", "ort": "12345 Anytown"}}' docker compose up --build
```

Or add it to `docker-compose.yml`'s `environment:` block directly. A
freeform "custom sender" option is also always available in the form for
one-off letters without touching config at all.

## Project layout

```
app/          FastAPI backend
  main.py       HTTP endpoints (generate, list senders, health)
  letters.py    CRUD for saved letters + PDF regeneration
  latex.py      Escaping, bilingual formatting, Jinja delimiters, pdflatex invocation (2-pass)
  schemas.py    Pydantic request models
  models.py     SQLModel schema (Letter)
  db.py         SQLite engine, data directories, self-migrating columns
  senders.py    Configurable quick-pick sender profiles
templates/    letter.tex.j2 -- the LaTeX document (g-brief class)
static/       Vanilla JS frontend, no build step
tests/        pytest -- LaTeX escaping/rendering, real-pdflatex compile checks, API
```

## Local development

For working directly on the backend with hot-reload, instead of in the
container:

Requirements: Python ≥ 3.13, [uv](https://docs.astral.sh/uv/), and a TeX
installation with `g-brief`, `ngerman`, and `babel`'s `english` language
(TeX Live: `texlive-latex-extra texlive-lang-german texlive-lang-english
texlive-publishers`).

```bash
uv sync
uv run uvicorn app.main:app --reload
```

### Tests

```bash
uv run pytest
```

The real-`pdflatex` compile tests are skipped automatically if `pdflatex`
isn't installed — everything else is mocked and runs everywhere.

### Environment variables

| Variable              | Default             | Purpose                            |
| ---------------------- | -------------------- | ----------------------------------- |
| `BRIEF_DATA_DIR`       | `./data`             | Base directory for all data         |
| `BRIEF_DB_PATH`        | `./data/brief.db`     | SQLite database file                |
| `BRIEF_SIGNATURE_DIR`  | `./data/signatures`   | Saved signature PNGs                |
| `BRIEF_SENDERS_JSON`   | one generic example   | Your own quick-pick sender profiles |

## Security notes

Every user-supplied field (recipient, subject, body) is LaTeX-escaped
before being interpolated into the `.tex` source (`app/latex.py`,
`escape_latex*`) — special characters render as literal text rather than
being interpreted as LaTeX syntax. `pdflatex` is always invoked with
`-no-shell-escape`, so even a successful escaping bypass couldn't run
shell commands via `\write18` — that's the actual hard security boundary;
escaping is defense in depth on top of it.

## License

MIT — see [LICENSE](LICENSE).
