FROM python:3.12.16-slim

RUN groupadd -g 10001 appuser && \
    useradd -u 10001 -g appuser -s /bin/sh -m appuser

WORKDIR /app

COPY services/simulator/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY services/simulator/src /app/src

ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SIM_CONFIG_DIR=/app/config

USER appuser

ENTRYPOINT ["python", "-m", "simulator"]
CMD ["publish", "--scenario", "/app/config/scenarios/demo.yaml"]
