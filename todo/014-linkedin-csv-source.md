# 014 — LinkedIn CSV source

## Goal
A safe, policy-compliant LinkedIn source: read a user-provided CSV. **No scraping.**

## Steps
1. `src/agents/outbound/sources/linkedin_csv.py`:
   - filters: `path`, `column_map` (defaults sensible for Sales Navigator exports).
   - Parses rows, normalizes columns, returns `ProspectCandidate`s.
2. Prompt file `src/llm/prompts/outbound/email_linkedin_csv.md` — tone: "you saw their profile, reference role + company".

## Verification
- `tests/test_linkedin_csv.py` with a tiny fixture CSV.

## Done when
- Tests pass. No code path makes a network request to LinkedIn.
