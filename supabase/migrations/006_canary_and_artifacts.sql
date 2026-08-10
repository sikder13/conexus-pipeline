-- Canary protocol state and outbound artifacts.
--
-- The safety model changed: outreach now runs without per-claim human
-- verification. The human is no longer upstream checking inputs; they are a
-- circuit-breaker on outputs. A circuit-breaker needs something it can actually
-- break, and that is the halt flag below — a row every send path must read
-- before it sends anything, ever.
--
-- The halt is deliberately a database row rather than a config file or an
-- environment variable. A file can be stale on one machine, and an env var is
-- invisible to anyone reading the data. This is the one shared piece of state
-- that stops the pipeline, so it lives where the pipeline already looks.

create table canary_state (
  id                  boolean primary key default true check (id),  -- single row
  halted              boolean not null default false,
  halt_reason         text,
  halted_at           timestamptz,
  batches_sent        smallint not null default 0,
  factual_corrections smallint not null default 0,
  inferable_eligible  boolean not null default false,
  updated_at          timestamptz not null default now()
);

insert into canary_state (id) values (true);

alter table canary_state enable row level security;

-- Generated outbound artifacts, with the gate's verdict attached. Stored rather
-- than regenerated so that what an operator reviewed is what would be sent, and
-- so a blocked artifact stays inspectable — a refusal nobody can read teaches
-- nobody anything.
create table outbound_artifacts (
  id                  uuid primary key default gen_random_uuid(),
  prospect_id         uuid not null references prospects(id) on delete cascade,
  kind                text not null check (kind in ('thesis', 'email', 'brief')),
  status              text not null check (status in ('sendable', 'blocked', 'draft')),
  body                text not null,
  gate_map            jsonb,          -- sentence -> claim path, per the outbound gate
  gate_failures       jsonb,          -- why it was blocked, if it was
  claims_cited        jsonb,          -- claim paths this artifact depends on
  attempts            smallint not null default 1,
  model               text,
  created_at          timestamptz not null default now()
);

create index idx_artifacts_prospect on outbound_artifacts (prospect_id);
create index idx_artifacts_status   on outbound_artifacts (status);

alter table outbound_artifacts enable row level security;

comment on table canary_state is
  'Single-row pipeline halt and canary progression. Every send path must check halted.';
comment on table outbound_artifacts is
  'Generated outreach with the outbound gate verdict. Blocked artifacts are kept.';
