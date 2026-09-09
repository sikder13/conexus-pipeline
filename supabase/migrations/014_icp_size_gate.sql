-- Company size decides eligibility, not just score.
--
-- Batesville Tool & Die held P1 at score 4 while its own case-study evidence
-- recorded 1,358 employees. Nothing threw an error. The extractor excludes a
-- giant when the SOURCE LISTING says so, and that check ran once at extraction;
-- the 1,358 arrived afterwards from the case study, and by then the only thing
-- reading size was the `too_big` scoring component, which is worth minus one
-- point. A company five times outside the ICP lost a point and came top.
--
-- A ceiling expressed as a penalty is not a ceiling. These three columns are
-- where the ceiling now lives.
--
-- `size_review` holds the reason a human has to decide, for the band between
-- the ICP ceiling and the point where the answer stops being arguable. It is
-- null when there is nothing to decide. A company carrying one is held out of
-- every outreach set.
--
-- `size_override` is the operator's note taking responsibility for a held
-- company. The note IS the override — there is no boolean, because a flag
-- somebody flipped tells a later reader nothing about why.
--
-- `size_band` is record-only for now: 'core' at 120 and under, 'growth' from
-- 121 to 250, null above the ceiling or where no trusted headcount exists. It
-- is written so that offer-tier routing has something to read when it is built,
-- and it changes no behaviour today.
--
-- Nothing here is backfilled. The score node writes all three on its next pass,
-- which is also the pass that applies the gate, so a value and the decision that
-- produced it arrive together.

alter table prospects
  add column if not exists size_band text
    check (size_band is null or size_band in ('core', 'growth')),
  add column if not exists size_review text,
  add column if not exists size_override text;

comment on column prospects.size_band is
  'core (<=120) | growth (121-250) | null. Offer-tier routing reads this; '
  'nothing else does yet.';
comment on column prospects.size_review is
  'Why an operator must decide this company''s ICP standing: a trusted T1/T2 '
  'headcount between 251 and 500. Null when there is nothing to decide. A '
  'company carrying one is excluded from every outreach set.';
comment on column prospects.size_override is
  'An operator''s note taking responsibility for a company held by size_review. '
  'The note is the override; there is deliberately no boolean.';

create index if not exists idx_prospects_size_review
  on prospects (size_review) where size_review is not null;
