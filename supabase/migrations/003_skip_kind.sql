-- ============================================================
-- 003_skip_kind.sql
-- Conexus-100 pipeline — distinguish permanent from transient skips
-- Nahl Technologies Inc. · August 2026
--
-- A node can skip a prospect for two very different reasons, and
-- until now the queue recorded them identically:
--
--   PERMANENT — nothing about this prospect will ever make the node
--   applicable. case_study skips the 502 companies that have no
--   case-study page; the Conexus site will not grow one for them.
--   Re-attempting these every run is pure waste.
--
--   TRANSIENT — the node could not run *this time*. The API key was
--   missing, the host was unreachable, an upstream field was not yet
--   populated. Re-attempting is exactly what should happen.
--
-- Collapsing the two stranded work: ten summary items skipped for a
-- missing ANTHROPIC_API_KEY became permanently unreachable, because
-- the runner's selector never listed 'skipped' in any branch —
-- including under --force.
--
-- skip_kind is null for rows written before this distinction existed.
-- The runner treats null as transient, which is the safe direction:
-- a wrongly-retried skip costs one cheap re-check, a wrongly-stranded
-- one costs the record.
--
-- Apply via: supabase db push
-- ============================================================

alter table work_items
  add column skip_kind text
  check (skip_kind is null or skip_kind in ('permanent', 'transient'));

comment on column work_items.skip_kind is
  'Why a skipped item was skipped: permanent (never re-attempt) or '
  'transient (re-attempt on the next run). Null on rows predating this '
  'column and treated as transient.';

-- Selecting the re-attemptable skips is now a hot path for every run,
-- alongside the existing (node_name, status) index.
create index idx_work_items_skip_kind on work_items (node_name, status, skip_kind)
  where status = 'skipped';
