# Model Starter

This is a password-protected web app. An analyst enters a ticker and chats with Claude, and Claude
builds a downloadable, formula-driven quarterly three-statement Excel model from SEC filings.
The team can suggest improvements. Claude turns each suggestion into a playbook edit or a GitHub
issue, and an admin approves it before it takes effect.

## Layout

- `app/main.py`: the FastAPI app.
  - Login uses a team password, and an admin password unlocks admin features. Sessions are signed cookies.
  - Chat sessions are polled for events. Downloads are confined to each session's `files/` folder.
  - Feedback and admin routes, and the static `web/` pages, are served from here too.
- `app/agent.py`: the Claude tool loop, one thread per chat turn.
  - Tools cover SEC lookup, filings, reading documents and XBRL quarterly facts, the model spec (`init_model_spec`, `set_spec_value`, `get_spec`), `build_model`, and server-side web search.
  - It uses the model from `CLAUDE_MODEL` (default `claude-opus-5-5`), streaming, context editing and prompt caching.
  - `recalc_and_check()` runs LibreOffice so the file opens with values, then reads the "Check:" rows.
- `app/builder/model_builder.py`: `build_workbook(spec, path)`. It turns a JSON spec into the workbook, after validating that history ties out.
  - The workbook tabs are Quarterly Model, Annual Model, Key Metrics, Competitors and Data Sources.
  - The spec format is in `app/builder/SPEC.md`, which the agent also reads.
- `app/sec.py`: EDGAR access. `quarterly_facts()` converts year-to-date facts into quarters (Q4 = FY − 9M) and snaps 52/53-week period ends to month ends.
- `app/playbook_default.md`: the starting playbook. The live copy lives under `DATA_DIR/playbook/`, is versioned, and is edited on the admin page.
- `app/feedback.py`: classifies feedback as playbook, code, both or none. On approval it applies the playbook edit or opens a GitHub issue that mentions `@claude`.
- `app/store.py`: file storage under `DATA_DIR`.
  - `app/persist.py` mirrors that storage to a private GitHub repo (`DATA_REPO`) for hosts without a persistent disk, such as Render's free plan.
  - It refuses to save to a public repo.
- `web/`: plain HTML, CSS and JS with no build step: `index.html` + `app.js`, `login.html` and `admin.html`.
- `examples/chrw_spec.json`: a complete, real spec for C.H. Robinson. It is the main test fixture.
- Deployment files: Netlify serves `web/` and forwards `/api/*` to the backend (`netlify.toml`). The backend is a Render Docker service on the free plan (`render.yaml`, `Dockerfile`).

## Commands

```bash
python -m venv .venv            # Windows: py -m venv .venv
. .venv/bin/activate            # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt pytest httpx
pytest -q                       # builder, SEC quarterizing, auth/admin, downloads, persistence
```

To run the site locally, see "Running locally" in README.md. It needs `ANTHROPIC_API_KEY` in the
environment, and you open http://localhost:8000.

To check a builder change end to end, build the CHRW example and recalculate it:

```bash
python -c "import json; from app.builder.model_builder import build_workbook; print(build_workbook(json.load(open('examples/chrw_spec.json')), 'chrw.xlsx'))"
soffice --headless --convert-to xlsx --outdir recalc chrw.xlsx   # then confirm every "Check:" row is 0
```

The one expected nonzero check is the historical cash check. Q3 FY2024 is off by 11 because of
held-for-sale cash.

## Rules from the owner (keep these)

- **Fiscal quarters, dated.** Every column shows the fiscal quarter label and its period-end date.
- **Five years back, five fiscal years forward.**
- **Source links go in clickable cells** on Data Sources, never in cell comments.
- **Outside driver data** must come from government or industry databases that publish at least quarterly, such as BTS, BLS, BEA, Census, FRED or industry associations.
- **Formula-driven:** drivers are inputs and everything else is a formula. The revolver plug avoids circularity by charging interest on beginning-of-period debt.
- **Spec sign conventions:** values are quarterly and in millions. Expenses are positive; cash outflows are negative.
- **Secrets:** the Anthropic API key and the passwords live only in host environment variables, never in the repo or in chat. This repo is **public**.
- **Admin approval** gates every change that comes from team feedback.

## Status

These have been checked:
- The builder against the CHRW spec, recalculated with LibreOffice.
- Login and admin gating, download confinement, and playbook versioning and revert.
- The GitHub mirror, against a fake API.
- Peak memory: about 310 MB, inside the 512 MB limit of Render's free plan.

These have not been run:
- A live chat with Claude: no API key was available in the build environment.
- Live SEC calls: the build environment's network blocked sec.gov.

So the first real run should be a full chat for one ticker, watching for tool errors. In
particular, check that `sec.py`'s filing index and document parsing work on real EDGAR pages.

Not deployed yet. When the owner is ready: Render free first, then Netlify. Follow the README steps,
including the private `model-starter-data` repo and a fine-grained token.
