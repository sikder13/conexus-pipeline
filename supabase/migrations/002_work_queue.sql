-- ============================================================
-- 002_work_queue.sql
-- Conexus-100 pipeline — node work queue
-- Nahl Technologies Inc. · August 2026
--
-- Research is modelled as NODES: small, idempotent units of work
-- with declared dependencies (normalize_identity, resolve_website,
-- and so on). This table is the queue that records, for every
-- (prospect, node) pair, whether that unit of work still needs
-- doing, is in flight, succeeded, or failed and why.
--
-- Why a table rather than a job queue service: the runner is
-- invoked by hand from the CLI, there is no scheduler, and the
-- interesting state is not "what is running" but "what did we
-- already learn about this company, and what is still missing".
-- Keeping that in Postgres next to the prospects means a run can
-- be interrupted, resumed, or re-pointed at a new node months
-- later without losing the audit trail.
--
-- The unique (prospect_id, node_name) constraint is what makes
-- nodes idempotent at the storage layer: a node can be enqueued
-- for a prospect exactly once, so re-running the extractor cannot
-- duplicate work.
--
-- Apply via: supabase db push
-- ============================================================

create type work_status as enum (
  'pending',   -- not yet attempted, or dependencies not yet met
  'running',   -- claimed by a runner process
  'done',      -- completed; result already merged into the prospect
  'failed',    -- attempted and raised; see last_error and attempts
  'skipped'    -- node decided this prospect is not applicable
);

create table work_items (
  id            uuid primary key default gen_random_uuid(),
  prospect_id   uuid not null references prospects(id) on delete cascade,
  node_name     text not null,
  status        work_status not null default 'pending',
  attempts      smallint not null default 0,
  last_error    text,
  started_at    timestamptz,
  completed_at  timestamptz,
  created_at    timestamptz not null default now(),

  -- One work item per node per prospect. This is the idempotency
  -- guarantee the runner and the extractor both lean on.
  unique (prospect_id, node_name)
);

-- ---------- INDEXES (the three queries the runner actually makes) ----------

-- "give me the pending items for node X" and "count by status for node X"
-- are the same access path: node first, then status.
create index idx_work_items_node_status on work_items (node_name, status);

-- "what is the state of every node for this prospect" — used when deciding
-- whether a node's dependencies are satisfied.
create index idx_work_items_prospect on work_items (prospect_id);

-- ---------- SECURITY (consistent with 001) ----------

alter table work_items enable row level security;
-- NO policies created => anon and authenticated roles can do NOTHING.
-- The runner connects with the service role key, CLI-side only.
