# --- Build Stage ---
FROM mcr.microsoft.com/playwright:v1.55.0-jammy AS base

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
# Copy the rest of the application source code
COPY . .

# Build the TypeScript code (assumes "npm run build" invokes tsc)
RUN npm run build

# Install Playwright dependencies (if not already installed via npm)
RUN npx playwright install-deps && npx playwright install --with-deps

# --- Runtime Stage ---
FROM mcr.microsoft.com/playwright:v1.55.0-jammy AS runtime

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

# Ensure the Playwright cache directory has proper permissions (adjust path if needed)
RUN mkdir -p /root/ms-playwright && chmod -R 777 /root/ms-playwright

# Copy an entrypoint script that will start Xvfb and run our bot
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Expose DISPLAY port for headful browser operations (adjust as needed)
ENV DISPLAY=:99

# Set the command to run our entrypoint script
CMD ["/app/entrypoint.sh"]
