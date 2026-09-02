FROM python:3.13-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY pyproject.toml ./
# gcc is required to build aiokafka's optional C speedup extension on
# python:3.13-slim (no prebuilt manylinux wheel for it yet as of aiokafka
# 0.11) -- removed again after install to keep the runtime image slim.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev zlib1g-dev \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir . \
    && apt-get purge -y --auto-remove gcc libc6-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

COPY app ./app
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts ./scripts

EXPOSE 8000

CMD alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000
