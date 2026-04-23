# Publishing the Twilize `.trex` extension

This runbook covers how to ship the Tableau Dashboard Extension
(`.trex` file + hosted backend) so that a Tableau Desktop / Server /
Cloud user can drop the `.trex` into a dashboard and get the same
filter-group / reference-image / manifest fixes that users on the MCP
path already enjoy.

> This is the output of **Route A** from the distribution plan — a
> surgical patch that brings the extension pipeline into parity with
> `twilize.pipeline.build_dashboard_from_csv`.  Route B (fold the
> extension into the mainline pipeline) remains the recommended
> longer-term refactor.

---

## What changed in Route A

Three code changes, plus one infra file:

1. `extension/backend/pipeline.py::generate_workbook`
   * New kwargs `reference_image_path: str = ""` and
     `required_charts: list[dict] | None = None`.
   * After the dashboard is built, calls
     `editor.apply_style_reference(image_path=...)` and
     `twilize.pipeline._link_global_filters(editor)`.
   * Returns a dict `{"output_path": ..., "manifest": {...}}` instead
     of a bare string.  The manifest matches the shape emitted by
     `build_dashboard_from_csv` (dashboards, filters,
     global_filter_groups, required_charts_fulfilled,
     style_reference, warnings).
2. `extension/backend/app.py`
   * `SuggestRequest` gains `required_charts: list[dict]`.  The
     request handler prepends those chart specs to `plan["charts"]`
     so they survive trim / dedup.
   * `GenerateRequest` gains `reference_image_base64: str` and
     `required_charts: list[dict]`.
   * `/api/generate` decodes the base64 image into a temp PNG/JPG,
     hands it to `generate_workbook`, emits the manifest on response
     header `X-Twilize-Manifest` (base64-encoded JSON), and writes a
     correlation id to `X-Twilize-Job-Id`.
   * New endpoint `GET /api/manifest/{job_id}` returns the same
     manifest as JSON for clients that can't read custom response
     headers.
3. `extension/frontend/src/utils/api.ts` +
   `extension/frontend/src/hooks/useGeneration.ts`
   * `suggestDashboard` accepts an optional `requiredCharts` list.
   * `generateWorkbook` accepts an optional `{ referenceImageBase64,
     requiredCharts }` options bag and now returns
     `{ downloadUrl, manifest, jobId }`.  The old signature (three
     positional args, `Promise<string>`) is no longer valid — callers
     inside this repo are updated; any downstream consumer needs the
     same shape bump.
4. `railway.extension.json`
   * New Railway config pointing at the existing repo-root
     `Dockerfile` (the full-stack extension image).  **Does not**
     touch the existing `railway.json`, which keeps serving
     `Dockerfile.mcp` for the MCP HTTP / Smithery stdio route.

An end-to-end regression test lives at
`extension/backend/tests/test_route_a_integration.py` and is expected
to pass on every future change to the trex pipeline.

---

## Deploy checklist

### 1. Pre-flight

```bash
# Run from repo root on Windows PowerShell:
uv run pytest extension/backend/tests/ -q -p no:cacheprovider
```

Every test in `extension/backend/tests/` must pass, including
`test_route_a_all_fixes_present`.

### 2. Provision a second Railway service

The existing `twilize-production.up.railway.app` service runs the MCP
HTTP server (see `railway.json` → `Dockerfile.mcp`).  The `.trex`
route needs a *separate* service running the full-stack extension
image.

1. In Railway → "New Project" → connect this repo.
2. Under Service → Settings → **Config-as-code Path**, set
   `railway.extension.json` (or copy its contents into the Railway UI).
3. Expected start command:
   `python -m uvicorn extension.backend.app:app --host 0.0.0.0 --port $PORT`
4. Health check: `GET /api/health` should return `{"status": "ok"}`.
5. Note the service URL, e.g.
   `https://twilize-extension-production.up.railway.app`.

### 3. Point the `.trex` manifest at the new URL

Open `extension/manifest/twilize-extension-production.trex` and
replace the `<url>` under `<source-location>` with the URL from step
2.5.  Bump `<version>` so Tableau Desktop re-downloads the manifest
instead of serving its cached copy.

```xml
<source-location>
  <url>https://twilize-extension-production.up.railway.app</url>
</source-location>
```

Do **not** check an `.trex` into git that still points at
`twilize-production.up.railway.app` — that URL serves the MCP HTTP
server, not the extension backend, so any Tableau user who loads it
will see a blank iframe.

### 4. Smoke test end-to-end

1. Open Tableau Desktop.
2. Dashboard → Extensions → Add Extension → pick the edited
   `twilize-extension-production.trex`.
3. Grant data access when prompted.
4. Attach a small reference image.
5. Generate.  In the resulting download you should see:
   * Every auto-filter on the dashboard applied with "All Using This
     Data Source" (not "Only This Worksheet") — verify by
     right-clicking any filter card on the dashboard and checking
     *Apply to Worksheets*.
   * The dashboard chrome reflects the reference image's palette
     (KPI card backgrounds, accent line, mark colors).
   * Opening the browser devtools Network tab on `/api/generate`
     shows an `X-Twilize-Manifest` header.  Base64-decode it and you
     should see a JSON blob including `filters.global_scope_applied:
     true` and a non-null `style_reference.extracted`.

### 5. (Optional) Publish the `.trex` for discovery

Tableau Exchange is the public catalogue for sanctioned extensions.
Submission requires:

* A signed `.trex` (Tableau provides a signing utility for verified
  publishers — see
  <https://tableau.github.io/extensions-api/docs/trex_signing.html>).
* A privacy policy and support URL on your hosting domain.
* A review round (typically 1-2 weeks).

While you wait, the unsigned `.trex` still works for any user who
drops it into *Dashboard → Extensions → Access Local Extensions*.
Share the file directly (email, Slack, Notion) and point people at
`docs/TREX_USAGE.md` for install steps if you have one; the file
itself is the artifact — there's nothing to `pip install`.

---

## Rollback

If anything explodes after a deploy:

1. `railway.extension.json`'s service can be paused or rolled back in
   the Railway UI without touching the MCP deployment.
2. The old behavior (no reference-image re-skin, filter-scope bug,
   string return from `generate_workbook`) can be restored by
   reverting the three extension source files listed under "What
   changed in Route A".
3. The `.trex` file itself is a trivial XML artifact — shipping a
   previous version to users is enough to roll the client back even
   if the server has moved on.

---

## Future: Route B fold-in

Route B replaces `extension/backend/pipeline.py::generate_workbook`
with a thin wrapper around `twilize.pipeline.build_dashboard_from_csv`
so the extension picks up future mainline fixes automatically. It is
the recommended structural follow-up once adoption of the `.trex`
route is validated.  Tracked informally at the top of
`extension/backend/pipeline.py` as a TODO.
