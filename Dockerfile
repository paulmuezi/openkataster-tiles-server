FROM python:3.12-slim

ARG OPENKATASTER_REVISION=unknown

LABEL org.opencontainers.image.revision="${OPENKATASTER_REVISION}" \
    org.opencontainers.image.source="https://github.com/paulmuezi/openkataster-tiles-server"

ENV OPENKATASTER_RELEASE_REVISION="${OPENKATASTER_REVISION}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /app/requirements.txt \
    && pip install --no-cache-dir pillow cryptography

COPY openkataster_tiles /app/openkataster_tiles
COPY build_alkis_search_index.py build_all_missing_search_sqlite.py /app/

EXPOSE 8080
CMD ["uvicorn", "openkataster_tiles.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "4"]
