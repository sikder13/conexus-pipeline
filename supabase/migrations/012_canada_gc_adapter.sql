-- The Canadian source: register the adapter, and give a prospect a region.
--
-- Numbered 012 rather than 010: this was written on a branch alongside
-- 010_market_context, which reached main first, and 011 belongs to the
-- LinkedIn artifact kind. Two files sharing a number is how a migration
-- gets applied twice or not at all.
--
-- Two changes, both small, both needed before a single Ontario or Alberta
-- company can be written.
--
-- 1. `source_adapters` gains the canada_gc row. The column on `prospects` has
--    referenced this table since migration 001, so an insert naming an
--    unregistered adapter is refused by the foreign key — which is the correct
--    behaviour and the reason this row is a migration rather than a fixture.
--
-- 2. `prospects` gains `region`. Until now every company was in Indiana and the
--    county column carried everything anyone needed to know about where it was.
--    A Canadian recipient has a province and no county, and putting "ON" in a
--    column called county would make every query about Indiana counties quietly
--    wrong. The column is nullable and stays null for every existing row,
--    because Indiana prospects have a county and the region would be a constant.
--
-- Scoring, compliance and peer grouping all now key off source_adapter, so the
-- registry row is load-bearing beyond the foreign key: lib/scoring.py and
-- lib/compliance.py raise rather than default when a source has no profile.

insert into source_adapters (id, display_name, region)
values (
  'canada_gc',
  'Government of Canada Grants & Contributions (proactive disclosure)',
  'Ontario and Alberta, CA'
)
on conflict (id) do nothing;

alter table prospects
  add column if not exists region text;

comment on column prospects.region is
  'The source''s own region label for the recipient: a Canadian province code '
  '(ON, AB) for canada_gc. Null for Indiana prospects, which carry a county '
  'instead. Never inferred — it is whatever the source published.';

create index if not exists idx_prospects_region
  on prospects (region) where region is not null;
