# Runnable VNC harness for the remote-browser login/validate flows.
# Inherits the join layer's runtime shape (Xvfb + humanized X11 + noVNC) — the SAME
# Linux + --password-store=basic environment the bot runs in, so a session logged in
# here is decryptable by the bot (macOS Keychain cookies are NOT portable to Linux).
# Build vexa/meet-join-env:dev first:  cd ../join && make env
FROM vexa/meet-join-env:dev

WORKDIR /pkg
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
# Browsers already ship in the base image — don't let npm re-download them.
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
COPY package.json package-lock.json* tsconfig.json ./
RUN --mount=type=cache,target=/root/.npm npm install --no-audit --no-fund
# src/ and scripts/ are baked as a baseline but MOUNTED live by the Makefile (tsx hot).
COPY src ./src
COPY scripts ./scripts

ENTRYPOINT ["bash", "scripts/docker-entrypoint.sh"]
