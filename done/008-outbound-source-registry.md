# 008 — Outbound source registry + manual_csv source

## Goal
Pluggable source system + the simplest source first (`manual_csv`). YouTube and LinkedIn-CSV come in later todos.

## Steps
1. `src/agents/outbound/sources/base.py` — `Protocol BaseSource` with `name: str` and `discover(filters) -> list[ProspectCandidate]`.
2. `src/agents/outbound/sources/manual_csv.py` — reads `filters.path`, expects columns `name, email, company, domain, country, notes`. Returns list of `ProspectCandidate`.
3. `src/agents/outbound/source_registry.py` — dict-based registry, `get_source(name) -> BaseSource`.
4. Pydantic models: `ProspectCandidate(name, email, company, domain, country, source, source_ref, extra: dict)`.

## Verification
- `tests/test_manual_csv.py` with a small CSV fixture asserts parsing.

## Done when
- Registry returns the manual_csv source, the source returns parsed rows.
