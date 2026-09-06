-- Scope-of-work analysis is a fourth kind of artifact.
--
-- The three existing kinds are all outbound: a thesis feeds a leave-behind, an
-- email and a brief are sent. The analysis is none of those. It is an internal
-- working document — the per-company scope of work the operator reads before a
-- call, carrying costed findings, three priced approaches and a peer position.
-- It is never shown to the company it is about.
--
-- Storing it beside the outbound kinds rather than in its own table is
-- deliberate: it is generated the same way, gated the same way, superseded the
-- same way, and the audit already reads every row here. A second table would
-- duplicate all of that so the word "outbound" in the table name could stay
-- literally true.
--
-- 'skipped' already means drafting was never attempted and carries its reason,
-- so a company below the evidence floor needs no new status — the analysis for
-- one is thin by construction and says so.

alter table outbound_artifacts drop constraint outbound_artifacts_kind_check;
alter table outbound_artifacts add constraint outbound_artifacts_kind_check
  check (kind in ('thesis', 'email', 'brief', 'analysis'));

comment on column outbound_artifacts.kind is
  'thesis | email | brief | analysis. The first three are outbound and pass the '
  'sentence-typing gate; analysis is internal-only and is held to source and '
  'distinctness rules instead.';
