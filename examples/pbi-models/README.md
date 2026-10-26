# Power BI semantic models

Each folder under `models/` is one Power BI model. CI captures its measures as metrics that
agents query through the metrics MCP server; the data stays in Databricks.

    models/<name>/<name>.pbix             first import, as Power BI Desktop saves it
    models/<name>/<name>.SemanticModel/   the model as TMDL (PBIP); once present it is the source
    models/<name>/warehouse.yaml          Databricks tables and columns (catalog, schema, tables, columns, sql)
    models/<name>/access.yaml             who is in each row-level security role (needed when the model has roles)

To change a metric, edit its measure in TMDL (or re-save the .pbix) and open a merge request.
The pipeline keeps one note on it: what changed, which measures were captured, which warehouses
were checked and each metric's verified total. Merging to main publishes a new `capture-v<N>`
release; the MCP server serves the newest one within a minute. To roll back, pin the server to an
earlier tag. A nightly schedule checks that the warehouse still has every mapped table and column
and keeps a `warehouse-drift` issue open while it does not.

CI files: `.gitlab-ci.yml` (tested), `azure-pipelines.yml` (untested template). The field guide,
with the warehouse.yaml / access.yaml reference and CI variables, is docs/powerbi/README.md in the
insurance-context repo.
