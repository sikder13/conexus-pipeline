-- A prospect can be skipped before drafting, and that is not a failure.
--
-- CASE-1 §6 sets a floor: three Tier-1 facts minimum, or the file never ships.
-- Below the floor there is nothing to write from, and a generator asked to try
-- anyway fills the empty space with reasoning the gate then refuses. Blocking
-- it there records the wrong cause: the draft was not bad, it should never
-- have been attempted.
--
-- 'skipped' is that distinction, stored rather than printed, so the reason a
-- company was passed over survives the run that decided it.

alter table outbound_artifacts drop constraint outbound_artifacts_status_check;
alter table outbound_artifacts add constraint outbound_artifacts_status_check
  check (status in ('sendable', 'blocked', 'draft', 'superseded', 'skipped'));

comment on column outbound_artifacts.status is
  'sendable | blocked | draft | superseded | skipped. Superseded rows are kept; '
  'skipped means drafting was never attempted, with the reason in gate_failures.';
