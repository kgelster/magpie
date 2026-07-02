# Magpie — two runtimes in one image:
#   Node  → the `ucp` CLI (@shopify/ucp-cli) that app.py shells out to
#   Python→ app.py itself (stdlib only, no pip)
FROM node:22-slim

# app.py needs a python3 interpreter; nothing from PyPI.
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 \
 && rm -rf /var/lib/apt/lists/*

# Pinned to the version tested locally (see README).
RUN npm install -g @shopify/ucp-cli@0.6.2

# ucp refuses to run without a local profile. It's pure protocol metadata (no
# secrets/keys), so the container mints its own fresh one at build time.
RUN ucp profile init --name agent --activate

WORKDIR /app
COPY app.py domain.py index.html privacy.html terms.html og-image.png hero-bg.jpg ./

# HOST=0.0.0.0 so Fly's proxy can reach the server; PORT matches fly.toml internal_port.
ENV HOST=0.0.0.0 \
    PORT=8080
EXPOSE 8080

CMD ["python3", "app.py"]
