# 033 — Outbound source filter customization (domain/country/size)

## Why

Each outbound source currently has its own ad-hoc filter shape:

- `youtube` accepts `query`, `region_code`, `min_subscribers`.
- `linkedin_csv` accepts only `path` + optional `column_map`; no
  follower / country / domain filtering.
- `manual_csv` accepts only `path`; no filtering at all.

We want a **consistent filter surface** so that operators can target
prospects by domain, country, and audience-size (subscribers for
YouTube, followers for LinkedIn) without learning a different config
per source. We also want to make sure every candidate carries enough
provenance (who they are, where we found them, how to reach them) for
the inbound team to follow up.

## What to do

1. Extend `ProspectCandidate` in `src/agents/outbound/sources/base.py`:
   - Add `role: str | None = None` (job title / channel category).
   - Add `audience_size: int | None = None` (subscribers / followers).
   - Keep `source`, `source_ref`, `extra` for arbitrary provenance.

2. Define a shared `SourceFilters` pydantic model:
   - `domains_allow: list[str] | None` (whitelist; e.g. `["example.com"]`).
   - `domains_block: list[str] | None` (blacklist).
   - `countries: list[str] | None` (ISO country codes; case-insensitive).
   - `min_audience: int | None` (subscribers / followers floor).
   - Per-source extras kept under `extra: dict`.

3. Each source's `discover()` accepts `SourceFilters` and applies the
   common filters after fetching:
   - `youtube`: `min_audience` replaces today's `min_subscribers`;
     `countries` filters by `snippet.country`; `domains_*` matches the
     email domain when present.
   - `linkedin_csv`: filter rows by `countries` (Location column),
     `domains_*` (Website or email), and `min_audience` (Followers
     column when available).
   - `manual_csv`: support all three filters on the email domain and
     a `country` column.

4. Update `source_registry` and any CLI/n8n wiring that calls
   `discover()` to pass `SourceFilters` instead of a raw dict.

## Acceptance criteria

- All three sources accept the same `SourceFilters` object.
- `ProspectCandidate` carries `role` and `audience_size` where the
  source can provide them.
- Existing outbound tests still pass; add unit tests for each filter
  (domain allow/block, country, min_audience) per source using fixture
  data.

## Verify

```bash
pytest tests/test_youtube_source.py tests/test_linkedin_csv.py \
       tests/test_manual_csv.py -v
```
