# 034 — Scrape commenters from competitor LinkedIn posts

## Why

A high-signal prospect pool: people who comment on competitor LinkedIn
posts are demonstrably interested in our category. The current
`linkedin_csv` source only ingests Sales Navigator exports — it cannot
discover these commenters at all. We want a new source that, given
one or more competitor post URLs, returns the commenters as
`ProspectCandidate`s.

## What to do

1. New source `src/agents/outbound/sources/linkedin_comments.py`
   exposing `name = "linkedin_comments"` and `discover(filters)` where
   `filters` includes:
   - `post_urls: list[str]` (required) — competitor post URLs.
   - `max_per_post: int = 50`.
   - Plus the standard `SourceFilters` from todo 033.

2. Two backends, selected at runtime:
   - **API backend** — only viable with a partner-tier LinkedIn API
     token; for most setups this will not be available, so feature-flag
     it behind `LINKEDIN_API_TOKEN`. If set, call the official endpoint
     and parse commenters.
   - **Playwright fallback** — default path. Use `playwright` (sync
     API) to open the post URL, scroll the comments pane, and extract
     each commenter's name, profile URL, headline, and (when visible)
     company. Credentials come from `LINKEDIN_SESSION_COOKIE` env var
     (li_at cookie copied from a logged-in browser) — do not store the
     password.

3. Provenance fields on each `ProspectCandidate`:
   - `source = "linkedin_comments"`.
   - `source_ref = <post URL>`.
   - `extra = {"profile_url": ..., "headline": ..., "comment_excerpt":
     ...}`.

4. Register the new source in `source_registry.py`.

5. Robustness:
   - Treat the Playwright path as best-effort: catch selector errors,
     log the failing URL, continue with the next post.
   - Respect a per-run cap on total prospects so we don't trip rate
     limits.
   - Hide behind `LINKEDIN_SCRAPING_ENABLED=false` by default; raise
     `NotImplementedError` from `discover()` when the flag is off,
     matching the WhatsApp pattern in `CLAUDE.md`.

## Acceptance criteria

- `linkedin_comments` is discoverable from `source_registry.get_source(
  "linkedin_comments")`.
- With the env flag off, calling `discover()` raises a clear error.
- With the env flag on and Playwright installed, a fixture HTML page
  (saved locally) yields the expected `ProspectCandidate`s. The unit
  test runs Playwright against `file://` URLs, never real LinkedIn.
- Documented in `.env.example` and `plan/` how to obtain the
  `LINKEDIN_SESSION_COOKIE`.

## Verify

```bash
pytest tests/test_linkedin_comments_source.py -v
```

## Risks / open questions

- LinkedIn ToS forbids scraping; flag this as **operator-responsibility**
  in `.env.example` and `README.md`.
- Selectors will drift; treat this source as needing periodic
  maintenance and add a `last_verified_at` constant in the file.
