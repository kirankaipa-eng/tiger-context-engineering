# The metrics MCP server: `ictx pbi serve --http 8080` over the newest capture-v* release (or --dir).
# Build it with `.venv/bin/python scripts/demo_pbi_gitlab.py image` (writes constraints.txt, the same
# duckdb/sqlglot pins as the capture image). Run it, e.g.:
#     docker run -p 8080:8080 -e GITLAB_TOKEN -e DATABRICKS_SERVER_HOSTNAME -e DATABRICKS_HTTP_PATH \
#       -e DATABRICKS_CLIENT_ID -e DATABRICKS_CLIENT_SECRET -e ICTX_OIDC_ISSUER -e ICTX_OIDC_AUDIENCE \
#       pbi-metrics:dev --gitlab https://gitlab.example.com --project group/pbi-models
# Arguments after the image name are added to `ictx pbi serve --http 8080 --host 0.0.0.0`.
# Without --keys / ICTX_PBI_KEYS or ICTX_OIDC_ISSUER every request is refused (401).
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /usr/local/bin/uv
WORKDIR /opt/ictx
COPY pyproject.toml README.md constraints.txt ./
COPY src ./src
RUN uv pip install --system --no-cache -c constraints.txt ".[powerbi,databricks,mcp]" \
 && useradd --system --uid 10001 --create-home --home-dir /home/ictx ictx
USER ictx
WORKDIR /home/ictx
EXPOSE 8080
# ?ping=0: liveness only, so the probe never wakes a stopped Databricks warehouse
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz?ping=0', timeout=4)"]
ENTRYPOINT ["ictx", "pbi", "serve", "--http", "8080", "--host", "0.0.0.0"]
