# conexus-pipeline

Internal tooling for Nahl Technologies' prospect research and outreach pipeline.

It takes public grant-recipient records — currently Conexus Indiana / IEDC
Manufacturing Readiness Grants — and turns them into evidence-backed outreach.
Every fact the pipeline records is stored as a *claim*: a value plus the source
it came from, the tier of that source, and the date someone checked. Claims
start unverified. A human promotes them in the Verifier before anything is said
to a prospect.

That constraint is the whole point. The system is built so that it cannot
quietly assert something it cannot show you the source for.

## How it fits together

```
supabase/migrations/   Schema. The contract everything else honours.
lib/                   Shared code. One definition of each rule.
  config.py            The only module that reads the environment.
  db.py                The only module that talks to Supabase.
  claims.py            Claim construction, tiers, evidence validation.
  scoring.py           Signal score and P1/P2/P3 priority. Pure functions.
tools/                 One package per tool, each runnable as a module.
tests/                 pytest suite for lib/. No network access.
data/raw/              Scratch space for fetched pages. Never committed.
```

Supabase Postgres is the only datastore. There is no ORM, no web framework and
no CRM.

### Source tiers

Tiers describe where a fact came from, never how confident anyone feels about
it. What a tier permits is fixed:

| Tier | Source | Permitted use |
| --- | --- | --- |
| T1 | The company's own words, or a government record | Assertable as fact, once verified |
| T2 | Reputable secondary press | Assertable **with** attribution |
| T3 | Aggregator estimate (headcount, revenue sites) | Internal filtering only. Never assertable |
| T4 | Our own inference | Must be labelled as a hypothesis |

`lib/claims.py` is the only supported way to build a claim. A database trigger
(`validate_evidence_claims`, in migration 001) rejects any claim written
without a tier, source URL and check date, so a buggy tool fails loudly rather
than writing an unsourced fact.

## Setup

### 1. Python 3.11 or newer

Ubuntu 22.04 ships Python 3.10, which is too old. Either add the deadsnakes
PPA:

```bash
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt update
sudo apt install python3.11 python3.11-venv
python3.11 -m venv .venv
```

or use [uv](https://docs.astral.sh/uv/), which installs a standalone
interpreter without root:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv python install 3.11
uv venv --python 3.11 .venv
```

### 2. Dependencies

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` mirrors `pyproject.toml`; either is fine. With uv, use
`uv pip install -r requirements.txt` (a uv-created venv has no `pip` of its
own).

### 3. Environment

```bash
cp .env.example .env
```

Fill in `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (dashboard → Project
Settings → API) and `CONTACT_EMAIL`. `ANTHROPIC_API_KEY` and
`CRAWLMOUSE_API_KEY` are optional; tools that need them say so at start-up.

`CONTACT_EMAIL` is required because it goes into the User-Agent sent on every
outbound request. Anyone whose server we touch gets a way to reach a person.

Missing a required variable fails at import with a message naming it. That is
deliberate: a crawl that dies halfway through for want of a key has already
wasted the crawl.

`.env` is gitignored. Do not commit it, and do not paste keys into issues.

### 4. Verify the install

```bash
python -m tools.smoke_test
```

See below for what it checks.

## Running the tools

Run everything from the repo root, with the venv active. Each tool is a
module:

```bash
python -m tools.smoke_test
```

| Tool | Command | Status |
| --- | --- | --- |
| Smoke test | `python -m tools.smoke_test` | Implemented |
| Extractor | `python -m tools.extractor` | Package scaffolded; entrypoint not yet written |
| Harvester | `python -m tools.harvester` | Package scaffolded; entrypoint not yet written |
| Verifier | `python -m tools.verifier` | Package scaffolded; entrypoint not yet written |
| Drafter | `python -m tools.drafter` | Package scaffolded; entrypoint not yet written |
| Logger | `python -m tools.logger` | Package scaffolded; entrypoint not yet written |

Each tool package gets a `main.py` entrypoint when it is built.

### What the smoke test does

1. Loads configuration and reports which optional keys are absent.
2. Connects to Supabase and counts rows in `prospects` and `source_adapters`.
3. Writes a prospect named `__SMOKE_TEST__` with a two-claim evidence file and
   reads it back, confirming the claims round-tripped unchanged.
4. Deliberately writes a claim with no tier and **requires the database to
   reject it**. A successful write here is reported as a failure, because it
   means the claim-shape backstop is not active and unsourced facts can reach
   the evidence file.
5. Deletes the test row and confirms none remain. Cleanup runs even when an
   earlier step has failed.

It exits non-zero if any step fails.

## Development

```bash
ruff check .     # lint; must be clean
pytest           # unit tests; must be green
```

Tests cover `lib/claims.py` and `lib/scoring.py` and make no network calls.
`ruff` is configured for a 100-character line length in `pyproject.toml`.

## Database and migrations

The schema lives in `supabase/migrations/`, as numbered SQL files. It is the
contract the Python code honours, and the migrations directory is the source of
truth for it.

Two rules:

- **Never edit a migration that has been applied.** Change the schema by adding
  a new numbered file, e.g. `002_add_contact_confidence.sql`.
- **`001_initial_schema.sql` has already been applied by hand**, via the
  Supabase SQL editor, to the live project. Do not run it again. It creates
  enums, tables and triggers unconditionally, so a second run fails partway
  through and leaves you guessing about what did and did not take. If you need
  to confirm what is applied, use `supabase migration list --linked`.

### Installing the Supabase CLI (Ubuntu)

The CLI is not distributed through apt. Install the release `.deb` directly:

```bash
curl -fsSLO https://github.com/supabase/cli/releases/latest/download/supabase_linux_amd64.deb
sudo dpkg -i supabase_linux_amd64.deb
rm supabase_linux_amd64.deb
supabase --version
```

Or, if you already run Homebrew on Linux:

```bash
brew install supabase/tap/supabase
```

### Linking to the project

`supabase/config.toml` already carries the project ref. Authenticate once, then
link:

```bash
supabase login                                  # opens a browser for an access token
supabase link --project-ref bpieuikyoivlqcontuqq
supabase migration list --linked                # confirm what the remote has
```

`supabase login` stores a personal access token outside the repo. In CI, set
`SUPABASE_ACCESS_TOKEN` instead.

### Applying future migrations

```bash
supabase migration new add_contact_confidence   # creates a timestamped file
# edit supabase/migrations/<new file>
supabase db push --dry-run                      # show what would run
supabase db push                                # apply to the linked project
```

`db push` applies only migrations the remote has not recorded. Because 001 was
applied by hand, the remote migration history may not list it; check with
`supabase migration list --linked` before your first push, and if 001 is
missing from the remote history, mark it as already applied with
`supabase migration repair --status applied 001` rather than letting `db push`
try to run it.

Running the local Supabase stack (`supabase start`) needs Docker and is not
required for any of this — the project talks to the hosted database directly.

## Access model

Migration 001 enables row level security on every table and creates no
policies, so the `anon` and `authenticated` roles can do nothing at all. The
service role key is the only way in, and it is used only from the CLI. There is
no browser client and no reason for one.
