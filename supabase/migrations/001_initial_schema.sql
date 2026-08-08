-- ============================================================
-- 001_initial_schema.sql
-- Conexus-100 pipeline — initial schema
-- Nahl Technologies Inc. · August 2026
-- Implements PIPE-1 §7 as amended (Pass B removed; machine
-- triage; Crawlmouse manual-only; feedback loop per CASE-1 §7)
-- Apply via: supabase db push  (or paste into SQL editor ONCE;
-- thereafter the migrations directory is the source of truth)
-- ============================================================

-- ---------- ENUMS ----------

-- Pipeline position. Priority (P1/P2/P3) is deliberately NOT in
-- this enum: priority can change without pipeline position
-- changing (weekly reweight per CASE-1 §7), so it lives in its
-- own column below.
create type prospect_stage as enum (
  'extracted',        -- S1 done: identity + website resolution
  'passA_done',       -- S2 Pass A evidence draft + score written
  'needs_review',     -- machine is UNSURE (identity/site/status) -> human glance
  'verified',         -- S2 Pass C: UD approved claims in Verifier
  'thesis_done',      -- S3: thesis drafted from verified fields only
  'contact_found',    -- S4: named human + channel path confirmed
  'outreach_active',  -- S5: sequence running
  'replied',
  'meeting',
  'proposal',
  'closed_won',
  'closed_lost',
  'dead'              -- confirmed closed / duplicate / out of ICP
);

create type prospect_priority as enum ('P1', 'P2', 'P3');

create type touch_channel as enum ('email', 'phone', 'linkedin', 'in_person', 'other');

create type touch_response as enum (
  'none', 'auto_reply', 'positive', 'neutral', 'negative',
  'referral', 'unsubscribe', 'bounce'
);

-- ---------- CORE TABLE ----------

create table prospects (
  id                  uuid primary key default gen_random_uuid(),

  -- Identity (S1)
  company_name        text not null,
  dba_name            text,
  county              text,
  city                text,
  website             text,
  website_confidence  smallint check (website_confidence between 0 and 100),
                      -- 0-100; <70 auto-sets stage='needs_review'
  industry_desc       text,
  naics_guess         text,

  -- Size estimates (Tier 3 unless noted — NEVER asserted to prospects)
  employee_estimate   text,          -- range string e.g. '21-50'
  employee_source     text,          -- source + tier, e.g. 'salary.com [T3]'
  revenue_estimate    text,
  revenue_source      text,

  -- Grant record (Tier 1 via IEDC/Conexus)
  grant_amount        numeric,
  grant_round         text,
  grant_year          smallint,
  tech_purchased      text,          -- what the grant funded, verbatim-adjacent
  has_case_study      boolean default false,  -- Conexus /case-study/ subpage exists
  case_study_url      text,

  -- Pipeline state
  stage               prospect_stage not null default 'extracted',
  priority            prospect_priority,       -- machine-assigned in Pass A
  priority_set_by     text default 'machine'
                      check (priority_set_by in ('machine','human')),
                      -- flips to 'human' if UD ever overrides — preserves
                      -- the audit trail of machine vs human judgment
  signal_score        smallint,
  score_breakdown     jsonb,
  -- shape: {"clerical_posting": 1, "data_gen_tech": 1, "case_study": 1,
  --         "weak_front_door": 0, "friction_reviews": 0,
  --         "decision_maker_found": 1, "in_drive_radius": 1,
  --         "too_big": 0, "status_uncertain": 0}
  -- Stored per-component so weights can be recalibrated retroactively
  -- from market feedback (CASE-1 §7) without re-researching anyone.

  machine_summary     text,          -- one-paragraph Pass A summary; UD's
                                     -- 30-second orientation at Pass C
  drive_minutes       smallint,      -- from Muncie, county-centroid estimate

  -- Evidence & work product
  evidence_file       jsonb,
  -- Every claim inside MUST be: {"value": ..., "tier": 1-4,
  --  "source_url": "...", "date_checked": "YYYY-MM-DD",
  --  "verified": bool, "verified_at": timestamptz|null}
  -- Shape enforced by Harvester on write and Verifier on approval;
  -- validate_evidence_claims() below is the DB-side backstop.
  ai_thesis           jsonb,         -- Drafter output; cites verified fields only
  contacts            jsonb,         -- [{name, role, email, email_confidence,
                                     --   phone, linkedin, source, tier}]
  crawlmouse          jsonb,         -- null until UD manually runs an audit;
                                     -- then {score, run_at, report_url, notes}

  -- Freshness & lifecycle
  freshness_date      date,          -- last verification date; >14 days old
                                     -- requires 10-min re-verify pre-outreach
  -- Outcome & learning loop (CASE-1 §7 — in schema from day one)
  outcome_value       numeric,       -- signed deal value if closed_won
  outcome_notes       text,
  research_minutes    smallint,      -- time spent, for time-vs-outcome review

  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);

-- ---------- TOUCHES + FEEDBACK ----------

create table outreach_touches (
  id                  uuid primary key default gen_random_uuid(),
  prospect_id         uuid not null references prospects(id) on delete cascade,
  touch_date          timestamptz not null default now(),
  channel             touch_channel not null,
  direction           text not null default 'outbound'
                      check (direction in ('outbound','inbound')),
  content_summary     text,
  sequence_step       smallint,      -- 1=day-1 email, 2=day-3 phone, etc.

  -- Feedback capture (the ratchet's fuel — CASE-1 §7)
  response            touch_response not null default 'none',
  corrections         jsonb,         -- what the prospect corrected in our math:
                                     -- [{"our_claim": "...", "their_number": "...",
                                     --   "field_path": "evidence_file.block8..."}]
                                     -- corrections = engagement AND calibration data
  quoted_back_blocks  text[],        -- which evidence blocks they referenced
                                     -- (those blocks earn more research time)
  objections          text[],
  next_action_date    date,
  created_at          timestamptz not null default now()
);

-- ---------- SOURCE ADAPTER REGISTRY (rollout-ready from day 1) ----------

create table source_adapters (
  id           text primary key,     -- 'conexus_iedc', 'feddev_ca', ...
  display_name text not null,
  region       text,
  created_at   timestamptz not null default now()
);
insert into source_adapters (id, display_name, region)
values ('conexus_iedc', 'Conexus Indiana / IEDC MRG', 'Indiana, US');

alter table prospects
  add column source_adapter text not null default 'conexus_iedc'
  references source_adapters(id);

-- ---------- CLAIM-SHAPE BACKSTOP (DATA-1 §2 rule 1) ----------

-- Tools enforce claim shape at write time; this function is the
-- DB-side backstop so a buggy tool can't silently write orphan
-- claims. Walks every object in evidence_file that has a "value"
-- key and requires tier/source_url/date_checked alongside it.
create or replace function validate_evidence_claims()
returns trigger language plpgsql as $$
declare
  bad_claims int;
begin
  if new.evidence_file is null then return new; end if;
  select count(*) into bad_claims
  from jsonb_path_query(new.evidence_file, 'strict $.** ? (exists(@.value))') c
  where not (c ? 'tier' and c ? 'source_url' and c ? 'date_checked');
  if bad_claims > 0 then
    raise exception 'evidence_file contains % claim(s) missing tier/source_url/date_checked (DATA-1 rule: no orphan claims)', bad_claims;
  end if;
  return new;
end $$;

create trigger trg_validate_evidence
  before insert or update of evidence_file on prospects
  for each row execute function validate_evidence_claims();

-- ---------- HOUSEKEEPING ----------

create or replace function touch_updated_at()
returns trigger language plpgsql as $$
begin new.updated_at = now(); return new; end $$;

create trigger trg_prospects_updated
  before update on prospects
  for each row execute function touch_updated_at();

-- ---------- INDEXES (query patterns we know we'll have) ----------

create index idx_prospects_stage        on prospects (stage);
create index idx_prospects_priority     on prospects (priority) where priority is not null;
create index idx_prospects_score        on prospects (signal_score desc nulls last);
create index idx_prospects_drive        on prospects (drive_minutes) where drive_minutes is not null;
create index idx_prospects_county       on prospects (county);
create index idx_touches_prospect       on outreach_touches (prospect_id, touch_date desc);
create index idx_touches_next_action    on outreach_touches (next_action_date)
                                        where next_action_date is not null;

-- ---------- SECURITY (lesson from the old site: locked from migration 001) ----------

alter table prospects        enable row level security;
alter table outreach_touches enable row level security;
alter table source_adapters  enable row level security;
-- NO policies created => anon and authenticated roles can do NOTHING.
-- All five tools connect with the service role key, server/CLI-side only.
-- This is internal tooling; there is no browser client, so there is
-- no reason any non-service role should ever touch these tables.
