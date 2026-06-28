# --- Build Stage ---
FROM mcr.microsoft.com/playwright:v1.56.0-jammy AS base

# Install required OS dependencies (e.g., for Xvfb and other utilities)
RUN apt-get update && apt-get install -y \
    xvfb \
    ffmpeg \
    x11-utils \
    pulseaudio \
    x11-xserver-utils \
    fluxbox \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Build context is the REPO ROOT (monorepo: the bot consumes the bricks).
# Bricks land where core's file:../../../modules/* resolve from /app
# (/app/../../.. normalizes to /).
COPY modules/join /modules/join
COPY modules/capture /modules/capture
COPY modules/recorder /modules/recorder
COPY modules/pipeline /modules/pipeline
COPY modules/speaker-attribution /modules/speaker-attribution
COPY modules/recording /modules/recording
RUN for b in join capture recorder pipeline speaker-attribution recording; do \
      cd /modules/$b && npm install --ignore-scripts && npm run build || exit 1; \
    done

# Copy package manifest and install dependencies with npm
# (no core package-lock.json exists on main; npm install, not ci)
COPY services/vexa-bot/core/package.json ./

RUN npm install
# Install TypeScript globally to avoid permission issues
RUN npm install -g typescript
# Copy the rest of the application source code
COPY services/vexa-bot/core/ ./

# Build the TypeScript code and browser utilities bundle
RUN npm run build

# Install Playwright dependencies (if not already installed via npm)
RUN npx playwright install-deps && npx playwright install --with-deps

# Install MS Edge for Teams support (skip on ARM64 - not supported)
RUN arch=$(uname -m) && if [ "$arch" = "x86_64" ] || [ "$arch" = "amd64" ]; then \
    npx playwright install msedge || true; \
    else \
    echo "Skipping msedge installation on $arch (not supported)"; \
    fi

# --- Runtime Stage ---
# Runtime inherits the meet-join brick's runtime shape (its source of truth:
# modules/join/Dockerfile.env) — Playwright + the humanized-X11 stack
# (xvfb·xdotool·xclip·x11vnc·novnc·websockify). The bot does NOT re-declare those
# (MANIFEST P6: service env = union of bricks' shapes, depended-on not duplicated).
# Build order: vexa/meet-join-env:dev must exist first (the deploy build builds
# brick env bases before service images).
FROM vexa/meet-join-env:dev AS runtime

# Set working directory
WORKDIR /app

# The bot's OWN extra runtime deps (NOT meet-join's): audio/recording (capture-kit
# territory), display extras, SSH, AWS CLI prerequisites.
RUN DEBIAN_FRONTEND=noninteractive apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ffmpeg \
    pulseaudio \
    xserver-xephyr \
    x11-utils \
    x11-xserver-utils \
    fluxbox \
    socat \
    autocutsel \
    openssh-server \
    sshpass \
    unzip \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install AWS CLI for S3/MinIO sync (used by authenticated bot mode)
RUN curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip \
    && unzip -q /tmp/awscliv2.zip -d /tmp \
    && /tmp/aws/install \
    && rm -rf /tmp/awscliv2.zip /tmp/aws

# Copy the built application, node_modules, and the brick (symlink target)
COPY --from=base /packages /packages
COPY --from=base /app /app

# Install MS Edge in runtime stage as well (skip on ARM64 - not supported)
RUN arch=$(uname -m) && if [ "$arch" = "x86_64" ] || [ "$arch" = "amd64" ]; then \
    npx playwright install msedge || true; \
    else \
    echo "Skipping msedge installation on $arch (not supported)"; \
    fi

# Ensure the Playwright cache directory has proper permissions (adjust path if needed)
RUN mkdir -p /root/ms-playwright && chmod -R 777 /root/ms-playwright

# Create storage directory structure for bot data (screenshots, logs, etc.)
RUN mkdir -p /app/storage/screenshots /app/storage/logs /app/storage/temp && chmod -R 777 /app/storage

# Copy an entrypoint script that will start Xvfb and run our bot
COPY services/vexa-bot/core/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Expose DISPLAY port for headful browser operations (adjust as needed)
ENV DISPLAY=:99

# Set the command to run our entrypoint script
CMD ["/app/entrypoint.sh"]
