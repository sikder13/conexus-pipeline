-- LinkedIn is a fifth kind of artifact, and it is outbound.
--
-- The operator sends by hand on two channels now: an email and, where the
-- person gate cleared a name, a connection note and a follow-up message. Those
-- are cold touches to a stranger, so they answer to the same gate the email
-- does — typed sentences, sourced facts, one hypothesis — and are stored beside
-- it rather than in some looser place.
--
-- What differs is length and furniture. A connection note is 280 characters and
-- a follow-up is 700, and neither carries the CAN-SPAM block: that is a rule
-- about email, and appending it to a LinkedIn note would be cargo cult.
--
-- 'analysis' stays the one internal kind. The check below is the place that
-- distinction is written down.

alter table outbound_artifacts drop constraint outbound_artifacts_kind_check;
alter table outbound_artifacts add constraint outbound_artifacts_kind_check
  check (kind in ('thesis', 'email', 'brief', 'analysis', 'linkedin'));

comment on column outbound_artifacts.kind is
  'thesis | email | brief | linkedin are outbound and pass the sentence-typing '
  'gate; analysis is internal-only and is held to source and distinctness rules '
  'instead.';
