# core/flows — one image, three entrypoints (worker · mailbox · api), selected by command.
# The engine is stdlib-pure; the deps here are the production edges only.
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir "sqlalchemy>=2" "psycopg[binary]>=3" "fastapi>=0.110" "uvicorn>=0.29"
COPY core/flows/schema.sql ./schema.sql
# THE MCP TOOL MANIFEST — what the gateway assembly fetches from
# `/.well-known/mcp-tools.json`. `flows_api` reads it at `parents[2]`, i.e. beside `src/`, so it has
# to be IN the image: without it the route answers 503 and the assembled MCP surface silently loses
# this domain's four tools. It is not under `src/` because it is the DOMAIN's declaration, read by
# the gateway's tests from the repo as well as by this service at runtime — one copy, two readers.
COPY core/flows/mcp.tools.v1.json ./mcp.tools.v1.json
COPY core/flows/src ./src
COPY behavior /behavior
ENV PYTHONPATH=/app/src \
    VEXA_BEHAVIOR_DIR=/behavior-private
# default: the worker; override command for the other two processes:
#   python -m flows_integrations.mailbox   ·   python -m flows_integrations.flows_api
WORKDIR /app/src
CMD ["python", "-m", "flows_worker"]
