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

# Copy package files and install dependencies with npm
COPY package.json package-lock.json ./

RUN npm install
# Install TypeScript globally to avoid permission issues
RUN npm install -g typescript
# Copy the rest of the application source code
COPY . .

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
FROM mcr.microsoft.com/playwright:v1.56.0-jammy AS runtime

# Set working directory
WORKDIR /app

# Install runtime dependencies for Xvfb and others (if needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    pulseaudio \
    xserver-xephyr \
    && rm -rf /var/lib/apt/lists/*

# Copy the built application and node_modules from the base stage
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
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Expose DISPLAY port for headful browser operations (adjust as needed)
ENV DISPLAY=:99

# Set the command to run our entrypoint script
CMD ["/app/entrypoint.sh"]
