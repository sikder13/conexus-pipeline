-- A one-page letter is a fifth outbound kind.
--
-- Not a shorter email and not a printed leave-behind. Its whole shape comes
-- from one bet: a page carrying ONE computed figure about the reader's own
-- operation, offered as a fragment they can correct, gets a reply that a
-- complete argument does not. A complete argument invites agreement or silence;
-- an incomplete one invites the missing number.
--
-- So the letter carries their grant as a Tier 1 anchor, one output of their
-- own financial model, a close that shifts the risk onto us by asking to be
-- corrected, and the founding-client line. Nothing else fits on the page, and
-- everything that does not fit is the point.
--
-- It is typed through the same outbound gate as the email, because it is the
-- same kind of thing: a cold touch to a stranger who did not ask to hear from
-- us. What differs is the channel it is delivered on, and the compliance
-- record stores that per artifact exactly as it does for every other kind.

alter table outbound_artifacts drop constraint outbound_artifacts_kind_check;
alter table outbound_artifacts add constraint outbound_artifacts_kind_check
  check (kind in ('thesis', 'email', 'brief', 'analysis', 'linkedin', 'letter'));

comment on column outbound_artifacts.kind is
  'thesis | email | brief | analysis | linkedin | letter. thesis, email, '
  'linkedin and letter are outbound and pass the sentence-typing gate; brief '
  'is the operator-facing companion; analysis is internal-only and is held to '
  'source and distinctness rules instead.';
