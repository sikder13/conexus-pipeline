-- Artifacts may be superseded rather than deleted.
--
-- A regeneration that erased its predecessor would hide what the generator used
-- to produce, and the whole reason this pipeline keeps blocked artifacts is that
-- a refusal nobody can read teaches nobody anything. The same argument applies
-- to a draft made obsolete by a prompt change.

alter table outbound_artifacts drop constraint outbound_artifacts_status_check;
alter table outbound_artifacts add constraint outbound_artifacts_status_check
  check (status in ('sendable', 'blocked', 'draft', 'superseded'));

comment on column outbound_artifacts.status is
  'sendable | blocked | draft | superseded. Superseded rows are kept.';
