# The image the models repo's CI runs in: insurance-context with the Power BI and Databricks extras
# (capture, verify on Databricks when DATABRICKS_* is set, drift). Build it with
#     .venv/bin/python scripts/demo_pbi_gitlab.py image     (writes constraints.txt into the context)
# and push it to a registry the runners can pull from; CI names it in CAPTURE_IMAGE.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /usr/local/bin/uv
WORKDIR /opt/ictx
COPY pyproject.toml README.md constraints.txt ./
COPY src ./src
# constraints.txt pins duckdb and sqlglot to what the MCP server runs, so the stand-in the
# pipeline writes opens there and the SQL it verified is the SQL that will be generated
RUN uv pip install --system --no-cache -c constraints.txt ".[powerbi,databricks]"
