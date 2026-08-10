-- Website integrity state — the second dimension, on the row itself.
--
-- A signal score answers "how interesting is this company". It cannot answer
-- "is this evidence about that company at all". Decatur Plastic Products scored
-- 4 and sat at P1 while its self_description held the text of an Indonesian
-- gambling site: the domain had expired, been re-registered, and redirected
-- through two other hosts to a togel page. Every claim read from it was
-- correctly formed and entirely false.
--
-- null means "not yet assessed", which is different from 'ok'. A row that has
-- never been checked must not read as one that passed.

alter table prospects add column website_status text
  check (website_status in ('ok', 'not_found', 'compromised', 'unreachable'));

-- What matched, when a fetch node decided the site was not the company's. The
-- audit requires at least one recorded fingerprint behind every 'compromised',
-- so a quarantine can always be argued with against the evidence.
alter table prospects add column website_fingerprints jsonb;

create index idx_prospects_website_status on prospects (website_status)
  where website_status is not null;

comment on column prospects.website_status is
  'ok | not_found | compromised | unreachable. Null = not yet assessed.';
comment on column prospects.website_fingerprints is
  'Markers that produced a non-ok website_status: [{marker, checked_at, url}].';
