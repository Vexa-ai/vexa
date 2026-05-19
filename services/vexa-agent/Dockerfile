FROM python:3.12-slim

# System dependencies: Node.js (for Claude CLI), git, curl, awscli (for MinIO sync)
RUN apt-get update && apt-get install -y --no-install-recommends \
        nodejs npm git curl ca-certificates && \
    npm install -g @anthropic-ai/claude-code && \
    pip install --no-cache-dir awscli httpx requests playwright && \
    apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /root/.npm

# System layer (read-only in practice)
COPY services/vexa-agent/system/bin/vexa /system/bin/vexa
RUN chmod +x /system/bin/vexa

ENV PATH="/system/bin:$PATH"

# Workspace templates (copied into /workspace on first init)
COPY features/knowledge-workspace/templates/knowledge/ /templates/knowledge/

# Git config for auto-commits
RUN git config --global user.email "vexa@agent" && \
    git config --global user.name "Vexa Agent"

WORKDIR /workspace

# Container stays alive for docker exec
CMD ["sleep", "infinity"]
