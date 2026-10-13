# RecallIndia web service: API + phone app, with Tesseract for receipt and label photos.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY recallindia ./recallindia
COPY data/reference ./data/reference
COPY data/seed ./data/seed
COPY data/samples ./data/samples

# Render and most hosts set $PORT. --init creates tables, loads the rules and, on an empty database,
# the bundled FSSAI recall snapshot.
CMD ["sh", "-c", "python -m recallindia serve --init --host 0.0.0.0 --port ${PORT:-8000}"]
