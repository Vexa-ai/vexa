# ==============================================================================
# Stage 1: Build native addon (Zoom SDK wrapper)
# ==============================================================================
FROM --platform=linux/amd64 node:20-bullseye AS native-builder

# Install build dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    python3 \
    libssl-dev \
    qtbase5-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/vexa-bot

# Copy package files for root workspace (build context is the REPO ROOT so the
# @vexa/* file: deps under modules/ are reachable — see stage 2).
COPY services/vexa-bot/package*.json ./
COPY services/vexa-bot/binding.gyp ./

# Copy Zoom SDK (if present) and native source
COPY services/vexa-bot/core/src/platforms/zoom/native/ ./core/src/platforms/zoom/native/

# Install dependencies without triggering native postinstall hooks.
# Native addon build is handled explicitly below on best-effort basis.
RUN npm install --ignore-scripts

# Build native addon (will skip if SDK not present)
RUN npm run build:native || echo "[Build] Native addon build skipped (SDK not available)"

# ==============================================================================
# Stage 2: Build TypeScript
# ==============================================================================
FROM mcr.microsoft.com/playwright:v1.56.0-jammy AS ts-builder

WORKDIR /app/vexa-bot

# Install TypeScript build dependencies
RUN apt-get update && apt-get install -y \
    xvfb \
    ffmpeg \
    x11-utils \
    pulseaudio \
    x11-xserver-utils \
    fluxbox \
    && rm -rf /var/lib/apt/lists/*

# Copy workspace files
COPY services/vexa-bot/core/package*.json ./core/
COPY services/vexa-bot/core/tsconfig.json ./core/
COPY services/vexa-bot/core/build-browser-utils.js ./core/

# Copy and build @vexa/* modules. Build context is the repo root so file: deps
# in core/package.json (e.g. file:../../../modules/join) resolve correctly.
# Build order: leaves first (no @vexa deps), then dependents.
RUN npm install -g typescript

# Leaf modules (no @vexa deps)
COPY modules/join/                             /modules/join/
COPY modules/recording/                        /modules/recording/
COPY modules/recorder/                         /modules/recorder/
COPY modules/remote-browser/                   /modules/remote-browser/
COPY modules/gmeet/capture/                    /modules/gmeet/capture/
COPY modules/mixed/capture/zoom/               /modules/mixed/capture/zoom/
COPY modules/mixed/capture/teams/              /modules/mixed/capture/teams/
COPY modules/shared/whisper/                   /modules/shared/whisper/
COPY modules/shared/buffer/                    /modules/shared/buffer/
COPY modules/shared/capture-codec/             /modules/shared/capture-codec/
COPY modules/shared/record-chunker/            /modules/shared/record-chunker/

RUN cd /modules/join && npm install --ignore-scripts && npm run build
RUN cd /modules/recording && npm install --ignore-scripts && npm run build
RUN cd /modules/recorder && npm install --ignore-scripts && npm run build
RUN cd /modules/remote-browser && npm install --ignore-scripts && npm run build
RUN cd /modules/gmeet/capture && npm install --ignore-scripts && npm run build
RUN cd /modules/mixed/capture/zoom && npm install --ignore-scripts && npm run build
RUN cd /modules/mixed/capture/teams && npm install --ignore-scripts && npm run build
RUN cd /modules/shared/whisper && npm install --ignore-scripts && npm run build
RUN cd /modules/shared/buffer && npm install --ignore-scripts && npm run build
RUN cd /modules/shared/capture-codec && npm install --ignore-scripts && npm run build || true
RUN cd /modules/shared/record-chunker && npm install --ignore-scripts && npm run build || true

# Dependent modules (require @vexa/transcribe-whisper and @vexa/transcribe-buffer)
COPY modules/gmeet/pipeline/                   /modules/gmeet/pipeline/
COPY modules/mixed/pipeline/                   /modules/mixed/pipeline/
RUN cd /modules/gmeet/pipeline && npm install --install-links --ignore-scripts && npm run build
RUN cd /modules/mixed/pipeline && npm install --install-links --ignore-scripts && npm run build

# Install bot TypeScript dependencies (--install-links copies file: bricks into
# node_modules so they survive the COPY to the runtime stage without dangling symlinks).
WORKDIR /app/vexa-bot/core
RUN npm install --install-links

# Pre-bake diarization ONNX models (~32 MB) into the transformers.js cache in
# node_modules — flows to the runtime stage with the node_modules COPY.
COPY services/vexa-bot/core/scripts/bake-diarization-models.js ./scripts/
RUN node scripts/bake-diarization-models.js

# Copy TypeScript source and assets (default avatar logo)
COPY services/vexa-bot/core/src/ ./src/
COPY services/vexa-bot/core/assets/ ./assets/

# Build TypeScript
RUN npm run build

# Install Playwright browsers
RUN npx playwright install-deps && npx playwright install --with-deps

# Install MS Edge for Teams support (x86_64 only)
RUN arch=$(uname -m) && if [ "$arch" = "x86_64" ] || [ "$arch" = "amd64" ]; then \
    cd /app/vexa-bot/core && npx playwright install msedge; \
    else \
    echo "Skipping msedge installation on $arch (not supported)"; \
    fi

# ==============================================================================
# Stage 3: Runtime
# ==============================================================================
FROM mcr.microsoft.com/playwright:v1.56.0-jammy AS runtime

WORKDIR /app/vexa-bot

# Install runtime dependencies
RUN export DEBIAN_FRONTEND=noninteractive && apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    pulseaudio \
    xserver-xephyr \
    libssl3 \
    libxcb-xtest0 \
    fluxbox \
    x11vnc \
    xdotool \
    xclip \
    novnc \
    python3-websockify \
    awscli \
    socat \
    curl \
    git \
    openssh-server \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /run/sshd \
    && sed -i 's/#PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config \
    && sed -i 's/#PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config

# Copy workspace package files (for runtime dependencies)
COPY --from=native-builder /app/vexa-bot/package*.json ./
COPY --from=native-builder /app/vexa-bot/node_modules ./node_modules

# Copy built native addon (if exists)
COPY --from=native-builder /app/vexa-bot/build ./build

# Copy Zoom SDK libraries (if present)
COPY --from=native-builder /app/vexa-bot/core/src/platforms/zoom/native/zoom_meeting_sdk ./core/src/platforms/zoom/native/zoom_meeting_sdk

# Copy TypeScript build artifacts and assets (default avatar logo)
COPY --from=ts-builder /app/vexa-bot/core/package*.json ./core/
COPY --from=ts-builder /app/vexa-bot/core/node_modules ./core/node_modules
COPY --from=ts-builder /app/vexa-bot/core/dist ./core/dist
COPY --from=ts-builder /app/vexa-bot/core/assets ./core/assets

# Install MS Edge in runtime stage (x86_64 only)
RUN arch=$(uname -m) && if [ "$arch" = "x86_64" ] || [ "$arch" = "amd64" ]; then \
    cd /app/vexa-bot/core && npx playwright install msedge; \
    else \
    echo "Skipping msedge installation on $arch (not supported)"; \
    fi

# Ensure Playwright cache has proper permissions
RUN mkdir -p /root/ms-playwright && chmod -R 777 /root/ms-playwright

# Create storage directories
RUN mkdir -p /app/vexa-bot/storage/screenshots /app/vexa-bot/storage/logs /app/vexa-bot/storage/temp && \
    chmod -R 777 /app/vexa-bot/storage

# Copy entrypoint script
COPY services/vexa-bot/core/entrypoint.sh /app/vexa-bot/entrypoint.sh
RUN chmod +x /app/vexa-bot/entrypoint.sh

# Set environment
ENV DISPLAY=:99
# Hot-debug build: expose Chromium CDP (opt-in flag, see core/src/constans.ts) so a
# human can drive the bot in via CDP. Harmless on prod images that don't set it.
ENV BOT_DEBUG_CDP=true

# Expose noVNC web interface and SSH for browser session mode
EXPOSE 6080 22

# Set working directory for runtime
WORKDIR /app/vexa-bot/core

# Run entrypoint
CMD ["/app/vexa-bot/entrypoint.sh"]
