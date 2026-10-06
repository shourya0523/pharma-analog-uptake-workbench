# Gold verification app

A small web app for a team checking gold rows against their sources. Each row
shows the figure, its quote and Claude's note beside the source document, with
the figure found and highlighted where the document allows.

It is gold-side tooling. The pipeline never reads it
(`backend/tests/test_verification_app_is_not_an_input.py`).

## What is in it

| Path | What it is |
|---|---|
| `scripts/build_rows.py` | Turns `seed/gold` into `data/rows.json`: every gold row that cites a source (quarterly, annual and companion figures, and the evidence for each exclusion), with a priority tier, reasons, Claude's note and a batch. |
| `supabase/schema.sql` | Tables, open access rules (no sign-in), a trigger that records the gold figure each reviewer was shown, `load_gold()`, and the progress views. |
| `supabase/functions/source` | Edge Function that fetches a row's source document for the in-app preview. It serves only URLs some gold row cites. Deploy with JWT verification off. |
| `web/` | The app: static HTML/JS, no build step. `config.js` names the Supabase project. Hosted from `shourya0523/gold-verification-app` (a copy of this folder) at https://gold-verification-app.vercel.app/. |
| `tests/` | `test_build_rows.py` (every sourced gold row is served once, unchanged) and `e2e/` (the whole app against a local stand-in for Supabase). |

## Using it

- **No sign-in.** On first visit, pick your name; the browser remembers it, and **Switch** changes it. Anyone with the address can use the app: it holds nothing sensitive.
- **Queue**: pick a tier (P1 first), filter to your batches or unassigned ones, **Claim** a batch (or set anyone as its assignee), and open it. To assign many at once, tick batches (or *Select all shown*) and use **Claim selected** or **Assign selected to…**.
- **Review**: the row is on the left, the document on the right.
  - `1` confirms. `F` then `2`–`6` flags a reason (wrong value, period or scope; not in the source; can't open it). Type the value you read and press `Enter`.
  - `J`/`K` move between rows. `O` opens the source in a separate tab, `/` searches inside the document, and Undo appears after every save.
- **Flags**: every row someone flagged, with all verdicts. Settle each as "gold is correct", "gold needs a fix" or "can't decide".
- **Progress**: counts by tier, kind and reviewer. Also the gold loader, and a CSV of all verdicts.
- **Export Excel** (top bar): downloads the tracker workbook as it stands: a checklist per product, every gold row with its verdicts, and every verdict.

## Running it

### Team members

Add someone by email and display name; they then appear on the first screen.
Ask Claude with the emails and names, or run this in the Supabase SQL editor:

    insert into team_members (email, display_name) values ('name@company.com', 'Name')
      on conflict (email) do update set display_name = excluded.display_name;

### Loading or refreshing gold

    python tools/verification-app/scripts/build_rows.py

Then in the app go to **Progress → Gold rows** and choose `tools/verification-app/data/rows.json`.

- **Matching:** the loader confirms the database holds exactly the file's rows.
- **Reloading after gold changes:** reloading keeps every verdict. Rows no longer in gold drop out of the queue. A verdict given against a figure that has since changed is marked for re-checking.

### One-time settings

- **SEC contact:** optional, and recommended. SEC asks automated clients to name a contact. The preview function reads one from `app_config`:

      insert into app_config values ('sec_contact', 'Team name contact@company.com')
        on conflict (key) do update set value = excluded.value;

### Exporting the tracker workbook

The export rebuilds `exports/gold_verification_tracker.xlsx` from current gold with the app's verdicts filled in. Run it whenever you want a fresh copy.

- **Reviewer columns:** Manually Verified, Verified By, Date Verified and Reviewer Notes, plus a reviewed-of-total count per product.
- **Rows sheet:** each row's verdicts and resolution.
- **Human Verdicts sheet:** every verdict on any kind of row, including whether the gold figure changed since it was given.

There are three ways to run it:

- **GitHub:** Actions → *Export gold tracker* → Run workflow. The workbook is attached to the run. Tick *commit* to also save the verdict snapshot to the branch. This needs the repository secrets `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`.
- **Locally, from Supabase:**

      SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... scripts/sourcing/export_gold_tracker.sh

- **Locally, without keys:** download the CSV from **Progress → Download all verdicts**, then run:

      scripts/sourcing/export_gold_tracker.sh gold-verdicts-YYYY-MM-DD.csv

Each run writes `docs/sourcing/human_verdicts.json`, a snapshot of the verdicts. Committing it lets anyone rebuild the same tracker from git.

### Tests

    cd backend
    ./.venv/bin/pytest -q ../tools/verification-app/tests tests/test_verification_app_is_not_an_input.py

The end-to-end run uses a local Supabase stand-in:

- **Stack:** Postgres with `schema.sql`, PostgREST, and the real Edge Function under Deno.
- **Steps:** it loads gold through the app's own button and drives the review flow in Chromium as two members and an outsider. It then compares every database row with gold.
- **Running it:** see the header of `tests/e2e/run_e2e.sh` for what it needs.

      tools/verification-app/tests/e2e/run_e2e.sh
