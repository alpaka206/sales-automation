# 013 — YouTube source adapter

## Goal
Real YouTube Data API integration as an outbound source.

## Steps
1. `src/integrations/youtube.py` — `YouTubeClient(api_key)` using httpx. Methods:
   - `search_channels(query, region_code, max_results=25)` (search.list, costs 100 units)
   - `get_channel(channel_id)` (channels.list)
2. `src/agents/outbound/sources/youtube.py — YouTubeSource.discover(filters)`:
   - filters: `query`, `region_code`, `min_subscribers`, `max_results`
   - Extract email from channel description if present; else mark `email=None`.
3. Quota guard: read from `data/cache/youtube/usage.json` to track cumulative cost per UTC day; abort if approaching 10,000 cap.
4. Prompt file `src/llm/prompts/outbound/email_youtube.md` referencing channel context.

## Verification
- `tests/test_youtube_source.py` with respx mocks the API and the email-extraction regex.

## Done when
- YouTube source produces candidates; quota tracker writes to disk.
