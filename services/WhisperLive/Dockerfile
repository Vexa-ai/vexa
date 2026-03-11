FROM python:3.10-slim

ENV DEBIAN_FRONTEND=noninteractive

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV OMP_NUM_THREADS=4

WORKDIR /app

COPY services/WhisperLive/requirements/server.txt /tmp/requirements.txt

RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY services/WhisperLive/ /app/

COPY services/WhisperLive/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

COPY services/WhisperLive/healthcheck.sh /healthcheck.sh
RUN chmod +x /healthcheck.sh

ENTRYPOINT ["/entrypoint.sh"]

CMD ["--port", "9090", "--backend", "remote"]

HEALTHCHECK --interval=15s --timeout=5s --start-period=90s --retries=3 CMD /healthcheck.sh
