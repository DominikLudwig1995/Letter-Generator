# Single image: FastAPI serves both the API and the static frontend
# (a plain HTML/JS file, no build step needed) -- see app/main.py's
# StaticFiles mount at "/".

# --- Backend deps ---
FROM docker.io/library/python:3.13-slim AS backend-builder
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv
# Built at the same path the runtime stage copies it to and runs it
# from (/app/.venv) -- uv bakes an absolute shebang into each console
# script (e.g. #!/build/.venv/bin/python for uvicorn), so a venv built
# under /build and copied to /app fails at container start with "exec
# /app/.venv/bin/uvicorn: no such file or directory".
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# --- Runtime ---
FROM docker.io/library/python:3.13-slim

# texlive-lang-german and texlive-publishers (for the g-brief document
# class itself) are the two packages this template actually needs on
# top of a base LaTeX install. lmodern/cm-super are Debian packages the
# texlive-* packages only *recommend*, not depend on --
# --no-install-recommends drops them, and pdflatex then fails to find
# them at compile time if a document ever needs them transitively.
RUN apt-get update && apt-get install -y --no-install-recommends \
    texlive-latex-base texlive-latex-recommended texlive-latex-extra \
    texlive-lang-german texlive-lang-english texlive-publishers \
    texlive-fonts-recommended cm-super lmodern \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    BRIEF_DATA_DIR=/data

COPY --from=backend-builder /app/.venv /app/.venv
COPY app/ app/
COPY templates/ templates/
COPY static/ static/

# Runtime data (SQLite db, saved signatures) lives on a volume mounted
# at /data.
RUN mkdir -p /data

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
