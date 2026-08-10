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
  nodes.py             The node contract and the polite fetch gate.
  runner.py            Dependency ordering, concurrency, result merging.
  geo.py               Indiana county drive-time estimates from Muncie.
  sources/             One adapter per public grant dataset.
tools/                 One package per tool, each runnable as a module.
  extractor/           Loads the source listing into prospects.
  runner/              CLI for executing nodes.
  harvester/nodes/     The research nodes themselves.
tests/                 pytest suite. No network access; all HTTP is mocked.
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
| Extractor | `python -m tools.extractor` | Implemented |
| Runner | `python -m tools.runner` | Implemented |
| Grant rounds | `python -m tools.grant_rounds` | Implemented |
| Audit | `python -m tools.audit` | Implemented |
| Report | `python -m tools.report` | Implemented |
| Verifier console | `python -m tools.console` | Implemented |
| Harvester nodes | run via `python -m tools.runner` | `normalize_identity`, `resolve_website` |
| Verifier | `python -m tools.verifier` | Package scaffolded; entrypoint not yet written |
| Drafter | `python -m tools.drafter` | Package scaffolded; entrypoint not yet written |
| Logger | `python -m tools.logger` | Package scaffolded; entrypoint not yet written |

Each tool package gets a `main.py` entrypoint when it is built.

### The usual working order

```bash
python -m tools.extractor --dry-run     # see what the source has, write nothing
python -m tools.extractor               # load prospects and queue their work
python -m tools.runner --status         # what is queued
python -m tools.runner                  # run every node over everything pending
```

### Extractor

Reads the Conexus Indiana recipient listing and loads it into `prospects`.

| Flag | Effect |
| --- | --- |
| `--dry-run` | Parse and report; write nothing |
| `--limit N` | Process only the first N companies |

Safe to re-run. Companies are matched on a normalised name, so a second run
updates existing rows instead of duplicating them, and it only overwrites the
columns this source owns — never a website a node resolved or a stage a human
set. Duplicates are collapsed and listed. Companies the source text says are
closed or enterprise-owned are marked `stage='dead'` with the reason in
`outcome_notes` rather than dropped, so there is a record of what we chose not
to pursue.

### Runner

| Flag | Effect |
| --- | --- |
| `--status` | Print the work queue and exit; makes no requests |
| `--nodes a,b` | Run only these nodes (default: all registered) |
| `--limit N` | Process at most N prospects per node |
| `--concurrency N` | Prospects in flight at once (default 8) |
| `--force` | Re-run items already marked `done` |
| `--include-permanent-skips` | Also re-run items skipped for a reason that cannot change |

### Grant rounds

Loads award amounts in bulk from the six Manufacturing Readiness Grant round
announcements (2020-2022), which list every recipient of a round on one page.
Six fetches instead of 572 per-company searches.

| Flag | Effect |
| --- | --- |
| `--dry-run` | Parse, match and report; write nothing |

Only an exact normalised-name match assigns an award — anything ambiguous is
reported for review rather than written. Where a case study and a round
announcement disagree on an amount, both are recorded with their sources and the
disagreement is flagged; the tool does not pick a winner. Companies that won in
more than one round keep every award.

The per-company format stops after 2022: later coverage reports the programme in
aggregate. A company whose only award came later keeps a null amount, which is
the correct answer.

### Audit

```bash
python -m tools.audit          # exits non-zero on any failure
```

Checks eight invariants against the live database — claim shape, source URLs,
score arithmetic, score traceability, stage discipline, queue reconciliation,
work-queue reachability, and that every P1 has a named human to call. It names
the offending rows rather than just counting them, and exits non-zero so it can
gate CI.

It exists because three separate bugs in this project shared one shape: the
system reported a healthy status while operating on the wrong data. None of them
raised an error; none would have been caught by a unit test.

### Report

```bash
python -m tools.report         # reads only, writes nothing
```

Prints the priority split across every prospect and again inside the ninety
minute drive radius, the contact-routing tiers, grant coverage by source tier,
and the ranked shortlist with a named contact for each. This is the view someone
reads instead of reading the database.

## Verifier console

```bash
python -m tools.console        # http://127.0.0.1:8000
```

The human gate. Everything before it is a machine's opinion; nothing the
pipeline gathered may be said to a company until a person has opened the source
and agreed. It is local only — it binds `127.0.0.1`, has no authentication and
is not deployed, because there is one user and no user model is needed.

Three screens: the **queue** (what is ready, what is half-finished, what is
parked for review), the **verify** screen (the claim worklist), and a read-only
**evidence view** for spot-checking any record.

### Keyboard map

The target is a file verified in ten to fifteen minutes without touching the
mouse except to read sources.

| Key | Does |
| --- | --- |
| `A` | approve the focused claim |
| `E` | edit its value — the original is kept, the source never changes |
| `K` | kill it — quarantined with a reason, never deleted |
| `?` | ask on the call — records it as a discovery question |
| `J` / `↓` | next claim |
| `L` / `↑` | previous claim |
| `Enter` | open the focused claim's source in a new tab |

Every disposition writes immediately. There is no batch save to lose.

### The floor check, and why each condition is there

`Mark verified` is the only code path in this repository that may set
`stage='verified'`. It refuses rather than warns, and it lists every unmet
condition at once rather than one per attempt:

- **Every claim has a disposition.** An undecided claim is one nobody read.
- **At least three approved T1 claims.** Below three first-party facts there is
  not enough to write an opening line a prospect would recognise as true. The
  block5 "check performed" marker does not count toward this — it records that
  we looked, not anything about the company.
- **At least one approved person claim**, and a person claim cannot be approved
  until its source link has been opened from the screen. The click is recorded
  server-side, because a rule a client can satisfy by claiming it did is not a
  rule. A fabricated name in an email greeting is the worst error this system
  can make.
- **At least one recorded gap** — an ask-on-call claim or a written note. A file
  with no open questions has usually been skimmed rather than read.
- **The block5 reviews check performed**, with findings or explicitly none.
  Performed-and-empty is a finding; not-performed is a hole, and collapsing the
  two turns a gap into an unnoticed assumption.
- **Evidence integrity still passing**, with no unresolved coherence issues.

Nothing in the console deletes anything. Killed and tainted claims keep their
value, source and date and gain only the reason they are no longer used, so the
record still shows what was believed and when it stopped being true.

`tools/audit.py` re-checks the guarantee from the other side: every
`stage='verified'` row must have a completed `verification_session`. That is
what keeps "only the console may verify" true after someone edits the console.

## What counts as a failure

A node run ends in one of three states, and the difference matters when reading
a run summary.

- **done** — the node learned something and it was written.
- **skipped** — the node correctly had nothing to do. A skip is *permanent* when
  the reason cannot change on its own (this company has no case study; robots.txt
  disallows the site) or *transient* when it can (the prospect is not P1 *yet*).
  Transient skips are retried by an ordinary run; permanent ones need
  `--include-permanent-skips`.
- **failed** — something went wrong that we did not intend.

Obeying a `robots.txt` disallow is recorded as a permanent skip, not a failure.
Declining to fetch a page we were asked not to fetch is the tool working. Filing
it as an error would inflate the failure rate with our own good behaviour and
leave the item retrying a request that must never be made.

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

## Node architecture

Research on several hundred companies is not one long script. It is many small
questions — what is this company called, where is its website, who runs it —
asked once per company, retried independently when they fail, and added to over
months. Each question is a **node**.

### What a node is

A node is a class with a name, an optional list of nodes it depends on, and one
async `run` method. It receives a prospect row and a `RunContext`, and returns a
`NodeResult` describing what it learned:

```python
class NodeResult(BaseModel):
    prospect_patch: dict   # columns to write on the prospect row
    evidence_patch: dict   # claims to merge into evidence_file
    notes: list[str]       # why the machine believes what it believes
    skipped: bool          # this node does not apply to this company
    skip_reason: str | None
```

A node is deliberately powerless. It does not touch the database, decide its own
retries, or write its result. The runner does all of that. That is what makes a
node testable without a database and safe to re-run.

Three rules a node must honour:

- **Be idempotent.** Running twice produces the same result and duplicates
  nothing. The runner merges rather than appends, but the node must not depend
  on running exactly once.
- **Never invent a value.** If a fact cannot be found, leave the key out and add
  a note saying what was looked for and where. A null with an explanation is a
  research task; a guess is a defect that reaches a prospect.
- **Never promote a prospect.** Setting `stage` to `verified` or anything later
  raises `StageViolation` and fails the item. Only a human in the Verifier moves
  a record past `passA_done`. `needs_review` and `dead` are fine.

### The Pass A node graph

```
normalize_identity ──┬─► case_study ───────────┐
                     ├─► grant_news ───────────┤
                     └─► resolve_website ──► front_door ──┬─► job_postings ──┤
                                                          └─► people ────────┤
                                                                             ▼
                                                                          score ──► summary
```

Nodes run in that order; prospects run in parallel within each node. Everything
a node learns lands in one of the eight evidence blocks, and no node writes into
another node's block.

| Node | Reads | Writes | Skips when |
| --- | --- | --- | --- |
| `normalize_identity` | nothing (pure) | `company_name`, `dba_name`, `county`, `drive_minutes`, `identity` claims | never |
| `resolve_website` | the source-published website | `website`, `website_confidence` | never |
| `case_study` | the Conexus case-study page | block2 (grant amount, award date, what it funded), block7 (people, verbatim quotes), block8 (headcount, capacity figures) | no case-study URL — 502 of 572 |
| `grant_news` | Inside INdiana Business site search | block2 (amount, round, year, 1:1-match floor), block7 (press quote), block8 (announced investment), `grant_amount` column | never; records the block unavailable instead |
| `front_door` | up to 8 pages of the company's own site | block1 (what they make, customers, model, certifications), block4 (SSL, viewport, forms, phone, address, broken links, careers URL), block6 (platform, embeds) | `website_confidence < 50` |
| `job_postings` | the careers URL front_door found | block3 (every open role with full duties), block6 (named ERP/MRP/CRM systems) | no careers page on their own site |
| `people` | about/team/contact pages, plus prior evidence | block7 (named people with roles) | never |
| `score` | the flags every block carries | `signal_score`, `score_breakdown`, `priority`, `score_evidence` | never |
| `summary` | tier 1–2 evidence only | `machine_summary` | priority outside P1/P2, or no `ANTHROPIC_API_KEY` |

Block 5 (customer friction) has no automated source. It is filled in by hand, so
the `friction_reviews` score component stays zero until a human works the record.

### Skips: permanent versus transient

A node that skips declares which kind of skip it is, and the runner stores it on
the work item:

- **Permanent** — nothing about this prospect will ever make the node
  applicable. `case_study` skipping a company with no case-study page. Left
  alone on ordinary runs *and* under `--force`; needs `--include-permanent-skips`.
- **Transient** (the default) — the node could not run *this time*: a missing
  credential, an unreachable host, an upstream field not yet populated.
  **Re-attempted on the next ordinary run, with no flag.**

Transient is the default deliberately. A skip wrongly marked transient costs one
cheap re-check; a skip wrongly marked permanent strands the record silently.
That is not hypothetical — ten summaries skipped for a missing `ANTHROPIC_API_KEY`
were unreachable by any flag until the two kinds were told apart. A `skip_kind`
of null predates the column and is treated as transient.

**A skipped dependency counts as satisfied.** `case_study` skips for the 502
companies without one, and `front_door` skips a prospect whose website could not
be identified — if a skip blocked the gate, `score` would never run for most of
the pipeline. A *failed* dependency blocks only while retries remain; once its
attempts are exhausted, downstream nodes proceed on the evidence that did
arrive, and the missing components score zero rather than erroring.

### Scoring

The scale, every threshold, and the dated reasoning behind each change live in
[docs/SCORING.md](docs/SCORING.md). Every change to a weight or a threshold
appends an entry there before it ships — a score is a claim about a company, and
a claim without provenance is what this pipeline exists to prevent.

### Scoring flags and traceability

Each score component is decided by a flag, and each flag is stored as a real
claim — with a tier and a source URL — inside the block that produced it. The
`score` node copies that provenance into a top-level `score_evidence` object, so
auditing a P1 means reading one object rather than the whole evidence file:

```json
"score_evidence": {
  "clerical_posting": {"points": 1, "flag": "has_clerical_posting", "tier": 1,
                       "source_url": "https://example.test/careers",
                       "detail": ["Order Entry Coordinator"]},
  "in_drive_radius":  {"points": 1, "flag": "drive_minutes column", "tier": 4,
                       "source_url": "https://www2.census.gov/...",
                       "detail": "28 minutes, within the 90 minute radius"}
}
```

Two components are read from prospect columns rather than flags, because the
node that would have set them may legitimately not have run: `in_drive_radius`
from `drive_minutes`, and `status_uncertain` from `website_confidence`.

### How to add one

Create a module under `tools/<tool>/nodes/`, and register the class:

```python
# tools/harvester/nodes/reviews.py
from typing import ClassVar

from lib.claims import Tier, make_claim
from lib.nodes import Node, NodeResult, RunContext, register


@register
class FindFrictionReviews(Node):
    """Look for customer-friction quotes in public reviews."""

    name: ClassVar[str] = "find_friction_reviews"
    depends_on: ClassVar[tuple[str, ...]] = ("resolve_website",)
    max_attempts: ClassVar[int] = 3

    async def run(self, prospect: dict, ctx: RunContext) -> NodeResult:
        website = prospect.get("website")
        if not website:
            return NodeResult(skipped=True, skip_reason="no website resolved yet")

        response = await ctx.fetch(f"{website}/reviews")
        # ... parse ...
        return NodeResult(
            evidence_patch={"reviews": {"quote": make_claim(quote, Tier.T2, source_url)}},
            notes=[f"read {source_url}; found 1 friction quote"],
        )
```

Then import it in `tools/harvester/nodes/__init__.py`. **A node that is never
imported is never registered, and a node that is never registered never runs.**
That import is the whole registration mechanism.

Fetch only through `ctx.fetch(url)`. It is the one sanctioned way to reach the
public web: it honours robots.txt (including a site's declared crawl-delay when
that is stricter than ours), serialises requests per host, attaches our real
User-Agent, and retries transient failures with backoff. A node that builds its
own HTTP client bypasses all of that, and will eventually get us blocked by
someone whose server we hammered.

### How the runner executes them

1. **Orders** the requested nodes so dependencies run first. A dependency cycle
   is detected up front and named, rather than deadlocking.
2. **Selects** work items for each node that are `pending`, `failed` with
   `attempts < max_attempts`, or stranded in `running` by a run that died.
   `--force` also re-runs `done` items.
3. **Defers** any prospect whose dependency nodes are not yet `done`. That item
   stays `pending` — an unmet dependency is not a failure, and it will run for
   free once the dependency lands.
4. **Runs** up to `--concurrency` prospects at once. Concurrency is across
   companies; requests to any single host are still strictly one at a time.
5. **Merges** the result on success: `prospect_patch` becomes a column update,
   `evidence_patch` is deep-merged into the existing `evidence_file`, notes are
   appended without duplicating, and the item is marked `done`.
6. **Records** the failure otherwise: attempts increments, the exception message
   goes into `last_error`, and the item is marked `failed`. One company failing
   never stops the run.

Evidence merging replaces whole claims rather than merging into them — a claim's
value, source and check date belong to the same observation and must not be
half-updated.

### Backfilling a new node across existing prospects

Nodes are enqueued per prospect at extraction time, so a node added later has no
work items for the companies already loaded. Create them by re-running the
extractor, which enqueues every registered node for every company it sees and
skips the ones that already exist:

```bash
python -m tools.extractor            # enqueues the new node for all 572
python -m tools.runner --status      # confirm the new node appears, all pending
python -m tools.runner --nodes find_friction_reviews --limit 20
```

Start with `--limit` on a new node. Twenty companies is enough to see whether
the confidence scores and notes look sane before pointing it at everything, and
a node that is wrong about 20 companies is a much cheaper mistake to undo.

To re-run a node whose logic changed:

```bash
python -m tools.runner --nodes resolve_website --force
```

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
- **`002_work_queue.sql` has also been applied** to the live project, and is
  recorded in the remote migration history. It adds the `work_status` enum and
  the `work_items` table behind the node runner. Same rule: do not re-run it.

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
