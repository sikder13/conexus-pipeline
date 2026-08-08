# conexus-pipeline — Project Instructions

Internal tooling for Nahl Technologies' prospect research and outreach
pipeline. Turns public grant-recipient records into evidence-backed,
human-verified outreach. This repo is also a demo asset: it may be shown
to prospects as proof of the AI-automation work we sell. Write it accordingly.

## Stack (locked — do not substitute)
Python 3.11+ · httpx · BeautifulSoup4 · pydantic v2 · rich (CLI UI) ·
anthropic SDK · supabase-py · python-dotenv · pytest. Supabase Postgres
is the only datastore. No ORM, no framework, no CRM.

## Commit and authorship rules (STRICT)
1. Commits are authored by the repository owner alone. NEVER add a
   `Co-Authored-By` trailer, a "Generated with" line, an AI tool name,
   or any emoji to a commit message.
2. Commit messages describe the change only: conventional commit prefix,
   imperative mood, plain English. Example: `feat: add signal score calculator`.
3. Never mention AI assistance, model names, or tooling in code comments,
   docstrings, README content, or PR descriptions.
4. Never commit `.env`, credentials, API keys, or any file under `data/raw/`.

## Data integrity rules (non-negotiable — these are the product)
5. Every factual claim written to `evidence_file` MUST be a dict with keys:
   `value`, `tier` (int 1-4), `source_url`, `date_checked` (YYYY-MM-DD),
   `verified` (bool), `verified_at` (ISO timestamp or None). A database
   trigger rejects claims missing tier/source_url/date_checked. Always
   construct claims via `lib/claims.py` helpers — never hand-build the dict.
6. Source tiers carry fixed meaning and must never be inflated:
   T1 = company's own words or government records (assertable as fact)
   T2 = reputable secondary press (assertable WITH attribution)
   T3 = aggregator estimates (NEVER assertable; internal filtering only)
   T4 = our own inference (must be labeled as hypothesis)
7. No tool may write `stage='verified'` or any later stage. Only the
   Verifier CLI, driven by a human, promotes a record past `passA_done`.
8. Public sources only. No scraping behind logins, no pretexting, no
   purchased personal data. Respect robots.txt. Identify the client
   honestly in the User-Agent. Rate-limit all outbound fetching.
9. Never invent, infer, or "fill in" a fact to complete a record. A missing
   value is recorded as null with a note. Fabrication is the single
   catastrophic failure mode of this system.

## Conventions
- `lib/` holds shared code; `tools/<name>/` holds one tool each with a
  `main.py` entrypoint runnable as `python -m tools.<name>`.
- Config comes from environment variables via `lib/config.py`. Never
  read `os.environ` directly outside that module.
- All database access goes through `lib/db.py`. No direct supabase client
  construction elsewhere.
- Type hints everywhere. pydantic models for anything crossing a boundary.
- Schema changes ONLY via new numbered files in `supabase/migrations/`.
  Never edit an applied migration.
- Every module gets a docstring explaining what it does and why it exists.

## Definition of done, every task
`ruff check .` clean · `pytest` green · the task's acceptance criteria
verified and stated back explicitly.
