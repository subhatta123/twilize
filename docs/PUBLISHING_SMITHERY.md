# Publishing twilize to Smithery

This document is the operational checklist for getting `twilize` listed on
the [Smithery MCP registry](https://smithery.ai) so Tableau users can install
the server in Cursor / Claude Desktop / VS Code / Claude Code / Windsurf with
a single click — no `pip install`, no terminal commands.

> ⚠️ **Ordering matters.** Smithery's install command is `uvx twilize`, which
> resolves to the *latest version published on PyPI*. If PyPI is behind the
> repo (as of this writing PyPI is at **0.33.0**, the repo's `pyproject.toml`
> is at **0.34.0**), **publish to PyPI first**, otherwise the Smithery
> listing will ship users an out-of-date build that is missing recent fixes
> (reference-image styling, global filter scope, filter-bar sizing, ...).

---

## 1. Confirm repo state

Before doing anything, make sure the repo is clean and the version you are
about to ship is bumped in both places:

```powershell
# Check that pyproject.toml and CHANGELOG.md agree on the version.
rg '^version' pyproject.toml
rg '^## \[' CHANGELOG.md | head -1
```

Expected output: both should name the same version (e.g. `0.34.0`). If the
repo has moved past the last tagged release (recent filter work, reference
image styling, ...), bump to the next SemVer-appropriate version and add a
`CHANGELOG.md` entry that summarises what's new since the last PyPI release.

Suggested rollup entry for the next release (post-0.34.0):

```markdown
## [0.35.0] - YYYY-MM-DD

### Added
- `csv_to_dashboard` now accepts `required_charts`, `reference_image`, and
  returns a structured manifest (`return_manifest=True`) so calling agents
  can verify what actually landed in the workbook instead of guessing.
- `apply_style_reference` now transfers card styling, spacing, typography,
  and chart mark colors from a reference image — not just the color theme.

### Fixed
- Filter bars no longer render as a 30 px sliver. Dashboards now allocate
  55 px of vertical space per filter row and proportionally shrink the
  chart area so the total dashboard height stays balanced.
- Dashboard filters marked "Apply to all worksheets using this data source"
  now actually apply globally. `_link_global_filters` stamps shared
  `filter-group` integers on every worksheet filter that references the
  same column, which is what Tableau's UI uses to detect scope.
- Invalid `mark-labels` / `mark-labels-color` attributes that caused
  Tableau error code D2E8DA72 on open have been removed from the style
  reference output.
```

---

## 2. Publish the new release to PyPI

From a clean checkout on the release commit:

```powershell
# Clean build artefacts
Remove-Item -Recurse -Force dist, build, src/twilize.egg-info -ErrorAction SilentlyContinue

# Build wheel + sdist
uv build

# Sanity-check the wheel has the expected files
python -m zipfile -l (Get-ChildItem dist/*.whl).FullName | Select-String "twilize/server.py"

# Upload to PyPI (requires a PyPI API token in ~/.pypirc or $env:TWINE_PASSWORD)
uv tool run twine upload dist/*
```

Then verify the upload landed:

```powershell
# Should print the new version, not the old one.
uv tool run --refresh --no-project --from requests python -c "import requests, json; print(json.loads(requests.get('https://pypi.org/pypi/twilize/json').text)['info']['version'])"
```

And smoke-test the stdio launch the way Smithery will invoke it:

```powershell
$env:MCP_TRANSPORT = "stdio"
uvx --refresh twilize    # should print nothing (stdio server waits for JSON-RPC on stdin)
# Ctrl+C to exit.
```

---

## 3. Tag and push

```powershell
git tag -a v0.35.0 -m "twilize 0.35.0"
git push origin main
git push origin v0.35.0
```

A GitHub Release is not required by Smithery but makes the listing look more
credible. Create one at
`https://github.com/subhatta123/twilize/releases/new?tag=v0.35.0` and paste
the CHANGELOG section for this version.

---

## 4. Submit to Smithery

Smithery supports two ways of onboarding a stdio MCP server. Use path A for
the simplest flow.

### Path A — One-click GitHub import (recommended)

1. Go to [smithery.ai/new](https://smithery.ai/new).
2. Sign in with GitHub. Grant read access to the `subhatta123/twilize` repo.
3. Paste the repo URL: `https://github.com/subhatta123/twilize`.
4. Smithery will auto-detect `smithery.yaml` at the repo root and read the
   `startCommand` block. It will render a preview of the launch command and
   the config form from `configSchema`.
5. Confirm the namespace — the default is `@subhatta123/twilize`. Feel free
   to move it under an organization namespace if you have one.
6. Submit.

Smithery runs a scan: it spawns the stdio server via `uvx twilize` in a
sandbox, calls `initialize` + `tools/list`, and records the tool inventory
for the server's landing page.

### Path B — Smithery CLI (when you need finer control)

```powershell
# Install the CLI once.
npm i -g @smithery/cli

# Log in.
smithery login

# Publish using the repo's smithery.yaml.
smithery publish --ns @subhatta123 --name twilize
```

The CLI version lets you iterate locally with `smithery dev` before
publishing.

---

## 5. Verify the listing

Open `https://smithery.ai/server/@subhatta123/twilize` and check:

- [ ] Tool count matches what twilize currently exposes (≈40 tools across 7
      categories as of 0.35.0). If Smithery shows 0 tools, the scan failed —
      open the deployment log from the server page and check that `uvx
      twilize` finishes cold-install in <60 s on a fresh runner.
- [ ] The "Install" button generates a valid config for Cursor and Claude
      Desktop. Copy the Cursor config and paste it into a real Cursor
      instance; confirm the server shows as "connected" and
      `csv_to_dashboard` appears in the tool list.
- [ ] The README preview on the listing page is not truncated. If it is,
      shorten the top banner or enable "show full README".

---

## 6. Update the README

Once Smithery confirms the namespace, drop the install badge into the
project README. The badge URL follows the pattern:

```markdown
[![smithery badge](https://smithery.ai/badge/@subhatta123/twilize)](https://smithery.ai/server/@subhatta123/twilize)
```

The README already has an "Install in one click" banner near the top with a
placeholder for this link — just replace `PLACEHOLDER-SMITHERY-URL` with the
final URL once you have it.

---

## 7. Common failure modes

| Symptom                                            | Cause                                                                  | Fix                                                                              |
|----------------------------------------------------|------------------------------------------------------------------------|----------------------------------------------------------------------------------|
| Smithery scan times out                            | PyPI is slow or `uvx twilize` has to resolve `tableauhyperapi` (~150 MB) | Pin `extras: ""` in the default config so the first-launch install stays small.  |
| Users get "version 0.33.0" after installing        | Forgot step 2 — PyPI was not bumped before submission                  | Publish the new version to PyPI, then bump the `version` default in Smithery's UI. |
| `uvx: command not found` on user machine           | User doesn't have `uv` installed                                       | Smithery's installer prompts to install `uv` automatically; document this in the README. |
| `csv_to_dashboard` fails on Smithery's sandbox     | Sandbox can't reach `C:\Users\...\Downloads\...` — expected            | That's fine; the sandbox only verifies `tools/list`, not full tool execution.    |
