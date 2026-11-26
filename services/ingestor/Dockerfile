FROM python:3.12.16-slim

RUN groupadd -g 10001 appuser && \
    useradd -u 10001 -g appuser -s /bin/sh -m appuser

WORKDIR /app

COPY services/ingestor/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY services/ingestor/src /app/src
COPY schemas /app/schemas

ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SCHEMA_DIR=/app/schemas

USER appuser

ENTRYPOINT ["python", "-m", "ingestor"]
