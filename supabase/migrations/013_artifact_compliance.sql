-- Which anti-spam regime governed an artifact, and on what basis.
--
-- CASL's exemption for a conspicuously published business address is a claim
-- about one message sent to one address: that the address was published, that
-- the publication carried no refusal, and that the message was relevant to the
-- recipient's role. A claim of that shape has to be recorded with the message
-- it is about, not asserted once in a document, because the question it answers
-- is asked about a specific email months later.
--
-- So every artifact stores its own verdict: the regime, the basis, whether it
-- passed, the address it was for, and the published addresses that were
-- available. A blocked artifact keeps it too — the reason a Canadian email was
-- refused is the most useful thing the gate produced that day.
--
-- Nullable because every artifact written before this migration was drafted
-- under CAN-SPAM for an Indiana prospect, and backfilling a verdict nobody
-- computed would be inventing a compliance record. Null means "not recorded",
-- which is the truth about those rows.

alter table outbound_artifacts
  add column if not exists compliance jsonb;

comment on column outbound_artifacts.compliance is
  'The outbound compliance verdict for this artifact: '
  '{regime, basis, passed, failures, target_address, published_addresses}. '
  'Written by lib/compliance.py. Null on artifacts drafted before 2026-09-08.';
