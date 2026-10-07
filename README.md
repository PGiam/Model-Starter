# Model Starter

A password-protected website where analysts enter a ticker, chat with Claude, and download a
quarterly three-statement Excel model built from SEC filings. The team can suggest improvements;
Claude drafts each change and an admin approves it before it goes live.

## What the site does

1. An analyst signs in with the team password and enters a ticker.
2. Claude pulls the company's filings and XBRL data from SEC EDGAR, proposes revenue drivers
   (company KPIs and quarterly government or industry series) and competitors, and asks what to add or remove.
3. Claude builds the workbook: **Quarterly Model** (IS, BS, CF together, five years back and five forward,
   fiscal quarters with dated columns), **Annual Model**, **Key Metrics**, **Competitors** and **Data Sources**
   (every source as a clickable link). The file is recalculated and its check rows are verified before it is offered.
4. The analyst asks for edits in chat; each rebuild is saved as a new version (`TICKER_Model_v2.xlsx`, ...).
   Filings Claude saved are downloadable too.

### How the site improves itself

- Anyone can click **Suggest an improvement**. Claude classifies the suggestion:
  - **Playbook change** (how models are built, which sources to prefer, formatting): Claude drafts the
    edited playbook and the admin page shows the diff. Approving makes it live for every new chat at once.
    Every version is kept and can be reverted.
  - **Code change** (new tab, new feature on the site): approving opens a GitHub issue that mentions
    `@claude`. The Claude GitHub Action (`.github/workflows/claude.yml`) implements it on a branch and opens a
    pull request. You review and merge it; Render redeploys automatically.
- Nothing changes without an admin approving it.

## Layout

| Path | What it is |
| --- | --- |
| `app/main.py` | Web API: login, chat sessions, downloads, feedback, admin |
| `app/agent.py` | The Claude conversation loop and its tools |
| `app/sec.py` | SEC EDGAR access (filings, XBRL facts, quarterizing year-to-date values) |
| `app/builder/` | The Excel model builder and the spec it reads (`SPEC.md`) |
| `app/playbook_default.md` | The starting playbook (the live copy is edited on the admin page) |
| `app/feedback.py` | Turns team suggestions into proposals; applies approved ones |
| `web/` | The pages Netlify serves |
| `examples/chrw_spec.json` | A complete C.H. Robinson spec used by the tests |

## Deploying (Netlify for the site, Render for the backend)

The backend runs long Claude conversations and LibreOffice, which Netlify Functions can't host
(they time out after seconds). So Netlify serves the pages and forwards `/api/*` to a small Render service.
To the browser it is one site on one address.

### 1. Backend on Render

1. Sign in at [render.com](https://render.com) with GitHub, choose **New > Blueprint**, and pick this repository.
   Render reads `render.yaml` and creates the `model-starter-api` service with a 5 GB disk.
   The Starter plan (about $7 a month) is needed for the disk; on the free plan files vanish on restart.
2. When Render asks for the environment variables, fill in:

   | Variable | Value |
   | --- | --- |
   | `ANTHROPIC_API_KEY` | Your key from [console.anthropic.com](https://console.anthropic.com). Paste it only here. |
   | `TEAM_PASSWORD` | The password analysts use |
   | `ADMIN_PASSWORD` | A different password for you (opens the admin page) |
   | `SEC_USER_AGENT` | Your firm name and a contact email, e.g. `Acme Research research@acme.com`. SEC requires it. |
   | `GITHUB_TOKEN` | Optional. A fine-grained token with **Issues: read and write** on this repo only, so approved code changes become issues. |

   `SESSION_SECRET` is generated for you; `GITHUB_REPO` is preset.
3. Deploy, then copy the service URL (e.g. `https://model-starter-api.onrender.com`) and open
   `/api/health` on it to check that it says `{"ok":true}`.

### 2. Site on Netlify

1. If your Render URL differs from `https://model-starter-api.onrender.com`, edit the `to =` line in
   `netlify.toml` and commit.
2. In Netlify choose **Add new site > Import an existing project**, pick this repository, and keep the
   defaults (no build command; publish directory `web` comes from `netlify.toml`).
3. Open the Netlify address, sign in with the team password, and enter a ticker.
   Optionally add your own domain under **Domain management**.

### 3. Let Claude open pull requests for approved code changes

1. Install the Claude GitHub App on this repository: <https://github.com/apps/claude>.
2. In the repository's **Settings > Secrets and variables > Actions**, add `ANTHROPIC_API_KEY`.

### 4. Keep costs in check

Each model costs a few dollars of Claude usage (more for companies with many segments).
The admin page shows estimated spend per model. Set a monthly spend limit in the Anthropic Console
under **Limits**.

## Other settings

| Variable | Default | Purpose |
| --- | --- | --- |
| `CLAUDE_MODEL` | `claude-opus-5-5` | Model used for chats and feedback |
| `CLAUDE_EFFORT` | `high` | Reasoning effort (`medium` is cheaper and faster) |
| `DATA_DIR` | `/data` in Docker | Where sessions, files and the playbook live |
| `COOKIE_SECURE` | `1` | Set to `0` only for local testing over http |
| `SOFFICE` | `soffice` | LibreOffice binary used for recalculation |

## Running locally

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest httpx
pytest -q
export ANTHROPIC_API_KEY=...  TEAM_PASSWORD=team  ADMIN_PASSWORD=admin  COOKIE_SECURE=0  DATA_DIR=./data \
       SEC_USER_AGENT="Your Name you@example.com"
uvicorn app.main:app --reload
```

Then open <http://localhost:8000>. Install LibreOffice so models are recalculated before download;
without it the workbook still works and Excel calculates it on open.

## Security notes

- The API key is read only from the server environment. It never reaches the browser or the repository.
- All pages' data comes from the API, which requires a signed session cookie; failed logins are rate limited.
- Everyone with the team password sees every model the team has built. Use the admin password only for admins.
