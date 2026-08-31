# core/flows — one image, three entrypoints (worker · mailbox · api), selected by command.
# The engine is stdlib-pure; the deps here are the production edges only.
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir "sqlalchemy>=2" "psycopg[binary]>=3" "fastapi>=0.110" "uvicorn>=0.29"
COPY core/flows/schema.sql ./schema.sql
COPY core/flows/src ./src
COPY behavior /behavior
ENV PYTHONPATH=/app/src \
    VEXA_BEHAVIOR_DIR=/behavior-private
# default: the worker; override command for the other two processes:
#   python -m flows_integrations.mailbox   ·   python -m flows_integrations.flows_api
WORKDIR /app/src
CMD ["python", "-m", "flows_worker"]
