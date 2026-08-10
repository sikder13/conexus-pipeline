-- Verification sessions — the record of a human sitting down with one file.
--
-- Two reasons this is a table rather than a column on prospects.
--
-- 1. A verification can be interrupted. Without a row saying "started but not
--    finished", a half-disposed evidence file is invisible: the prospect still
--    reads passA_done and goes back into the queue as if untouched, and the
--    twenty minutes already spent are lost. The console's IN PROGRESS section
--    reads exactly this.
-- 2. research_minutes on the prospect is an accumulating total and cannot say
--    how many sittings it took or what happened in each. The per-session
--    counts are what a later time-versus-outcome review needs.
--
-- The session is also the enforcement record for the floor check: a prospect at
-- stage='verified' must have a completed session, and tools/audit.py checks it.
-- That is what makes "only the console may verify" auditable after the fact
-- rather than merely true of the code today.

create table verification_sessions (
  id                  uuid primary key default gen_random_uuid(),
  prospect_id         uuid not null references prospects(id) on delete cascade,
  started_at          timestamptz not null default now(),
  completed_at        timestamptz,            -- null while in progress

  -- Per-session disposition counts. Written on completion; they describe this
  -- sitting only, so several sessions on one prospect stay individually legible.
  claims_total        smallint not null default 0,
  claims_approved     smallint not null default 0,
  claims_killed       smallint not null default 0,
  claims_edited       smallint not null default 0,
  claims_questioned   smallint not null default 0,
  duration_seconds    integer,

  -- Source links opened during this session, as claim paths. The block7
  -- source-open rule is enforced against this list: a person claim cannot be
  -- approved until its source has actually been opened from the verify screen.
  -- Kept server-side deliberately — a client-side flag would be a suggestion,
  -- and this rule exists to stop a fabricated name reaching an email greeting.
  opened_sources      jsonb not null default '[]'::jsonb,

  -- Free-text gaps the verifier recorded: what we still do not know and should
  -- ask on the call. The floor check requires either one of these or an
  -- ask-on-call claim, because a file with no open questions has usually been
  -- skimmed rather than read.
  gaps                text,

  created_at          timestamptz not null default now()
);

create index idx_sessions_prospect on verification_sessions (prospect_id);
create index idx_sessions_open     on verification_sessions (prospect_id)
                                   where completed_at is null;

alter table verification_sessions enable row level security;

-- Why a prospect is sitting in needs_review, in the row itself rather than only
-- in an evidence note. The console lists these and a human has to be able to
-- read the reason without opening the file.
alter table prospects add column needs_review_reason text;

-- What the integrity gate found, when it failed. Stored rather than recomputed
-- so the console shows the same verdict the score node acted on.
alter table prospects add column integrity_report jsonb;

comment on table verification_sessions is
  'One human sitting with one evidence file. A completed row is the required '
  'evidence that stage=verified was set by the console floor check.';
comment on column prospects.needs_review_reason is
  'Why this row needs a human glance, in plain words.';
comment on column prospects.integrity_report is
  'Evidence-integrity verdict: {passing: bool, failures: [str], checked_at}.';
