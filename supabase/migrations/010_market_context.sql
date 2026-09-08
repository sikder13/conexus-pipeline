-- Market context is a property of a segment, not of a company.
--
-- The demand direction for metal fabrication is the same sentence for every
-- metal fabricator in the dataset. Holding it on each prospect would make one
-- request per company for one answer, and would let sixteen companies end up
-- carrying sixteen slightly different versions of a fact that has one version.
--
-- So it is keyed by industry family and read by every company placed in that
-- family. The family key is the same one lib/peers.py assigns, which is what
-- makes the peer group and the market paragraph describe the same segment
-- rather than two adjacent ones.
--
-- `statements` holds what survived the verbatim-quote check in lib/market.py:
-- each entry names its source and carries the quote it was extracted from, so
-- a reader can open the page and find the sentence. `discarded` holds what did
-- not survive and why. Keeping the discards is the same argument as keeping a
-- blocked artifact — a refusal nobody can read teaches nobody anything, and on
-- a first run the discards are the most informative half.
--
-- `note` carries 'no reliable market context found' for a family whose sources
-- yielded nothing. That is a recorded absence, and it is the correct output
-- when nothing passed the check.

create table market_context (
  family              text primary key,
  statements          jsonb not null default '[]'::jsonb,
  sources_read        jsonb not null default '[]'::jsonb,
  discarded           jsonb not null default '[]'::jsonb,
  note                text,
  model               text,
  fetched_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);

alter table market_context enable row level security;

comment on table market_context is
  'Per-industry-family market context, cached and reused across every company '
  'in that family. Every statement carries a verbatim quote checked against the '
  'page it names; a family with none carries the note instead.';
comment on column market_context.discarded is
  'Statements dropped because their quote was not found in the source. Kept: a '
  'refusal nobody can read teaches nobody anything.';
