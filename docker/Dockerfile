# webSpell MCP server image. The build context is the repository (or release ZIP) root.
#
# Stage 1 produces the wheel: from source in a git checkout, or the prebuilt wheel
# shipped in the release ZIP (dist/), which has no source tree.
FROM python:3.12-slim AS wheel
WORKDIR /src
COPY . .
RUN set -eu; mkdir -p /wheels; \
    if [ -d webspell ] && [ -f pyproject.toml ]; then \
        pip install --no-cache-dir "hatchling>=1.27" && \
        pip wheel --no-cache-dir --no-deps --no-build-isolation -w /wheels . ; \
    else \
        cp dist/webspell-*-py3-none-any.whl /wheels/ ; \
    fi; \
    test "$(ls /wheels/webspell-*-py3-none-any.whl | wc -l)" -eq 1

# Stage 2 installs exactly that wheel with the server and pdf extras.
FROM python:3.12-slim

# trafilatura needs libxml2/libxslt for its lxml backend
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2 libxslt1.1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=wheel /wheels /wheels
COPY docker/constraints.txt /tmp/constraints.txt
RUN set -eu; wheel="$(ls /wheels/webspell-*-py3-none-any.whl)"; \
    pip install --no-cache-dir -c /tmp/constraints.txt "${wheel}[server,pdf]"; \
    rm -rf /wheels /tmp/constraints.txt

COPY THIRD_PARTY_NOTICES.md LICENSE /usr/share/doc/webspell/
WORKDIR /app
COPY docker/settings.toml /app/settings.toml

ENV MCP_HOST=0.0.0.0 \
    MCP_PORT=8765 \
    MCP_SETTINGS_FILE=/app/settings.toml
EXPOSE 8765
CMD ["python", "-u", "-m", "webspell"]
