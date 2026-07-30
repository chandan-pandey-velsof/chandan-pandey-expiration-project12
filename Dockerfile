# =============================================================================
# EaseIP PTA Tool v3 — image for the TIP "Develop on Local" platform
# -----------------------------------------------------------------------------
# The platform runs ONE container and exposes ONE public port: 8000 (HTTP).
# TLS/HTTPS is terminated by the platform's proxy, so the app must serve plain
# HTTP on 8000 and must NOT start its own HTTPS server (app.py's __main__ block
# would otherwise bind HTTPS on 9092 — we bypass it by launching via gunicorn).
# =============================================================================
FROM python:3.11-slim

# System libraries the PDF/OCR pipeline depends on:
#   poppler-utils  -> pdf2image / pdfinfo      (PDF rasterisation)
#   tesseract-ocr  -> pytesseract              (OCR)
RUN apt-get update && apt-get install -y --no-install-recommends \
        poppler-utils \
        tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first so this layer is cached across code-only changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# App source: app.py, pta_engine.py, templates/, static/, config.json
COPY . .

# The platform requires the app to listen on port 8000.
EXPOSE 8000

# Run Flask through gunicorn (production WSGI server) on plain HTTP.
# `app:app` imports the module-level Flask object from app.py and therefore
# SKIPS app.py's __main__ HTTPS block entirely.
#   --workers 2   : light concurrency for previews
#   --timeout 180 : OCR / large-PDF requests can run long
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "180"]
