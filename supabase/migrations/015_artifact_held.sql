-- An artifact can be withheld after the fact, and that is not a gate failure.
--
-- The drafting floor asks for three Tier-1 facts about the company. Until
-- 2026-09-10 the pool it counted included our own derivations — scoring flags
-- and labels we computed — so companies cleared the floor on claims that could
-- never fail a check because they were never checkable. Trifecta Medical
-- cleared it on two real claims and `has_case_study`, a boolean of ours.
--
-- Correcting the pool deflated the floor, and it deflated it retroactively:
-- artifacts already written and already passing their gates turned out to have
-- been generated for companies that did not qualify. Those artifacts are not
-- wrong in the way `blocked` means — their prose passed every check — and they
-- are not `skipped`, because they were attempted and finished.
--
-- 'held' is that third thing. The artifact stays exactly as written, with the
-- reason and its previous status recorded in gate_map so the hold can be lifted
-- when the evidence catches up rather than re-litigated from memory.

alter table outbound_artifacts drop constraint outbound_artifacts_status_check;
alter table outbound_artifacts add constraint outbound_artifacts_status_check
  check (status in ('sendable', 'blocked', 'draft', 'superseded', 'skipped', 'held'));

comment on column outbound_artifacts.status is
  'sendable | blocked | draft | superseded | skipped | held. Superseded rows are '
  'kept; skipped means drafting was never attempted; held means it was written '
  'and passed, and the company later turned out to be below the evidence floor.';
