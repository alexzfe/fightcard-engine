# TDD evidence: enum values are closed within /v1/

**Source:** the api-design / contract-first review of the site and this engine (2026-10-27). Decided: an existing
enum's values are part of the contract; a new value is `/v2/`. **Branch:** `docs/closed-enums` (from `main` at ca103af).
**Depends on:** the site's `feat/contract-checks` (fetch_v1 skips unknown files; the site checks its schema
against `/v1/schema.json`). Merge that first: these docs say the site already skips.

| Task | Summary | RED (0328500) | GREEN (7804b05) |
|---|---|---|---|
| Enums closed | CONTRACT.md Versioning lists the enums and says a new value is `/v2/`; API.md says so for clients. | 2 failed | pass |
| Pinned enums | `tests/publish/test_v1_enums.py` walks `schema()` and compares every enum to a frozen map, so a new `Literal` value fails CI here before it reaches a consumer. | passed already (a guard on today's values) | pass |
| Unknown files | CONTRACT.md: the site's fetch_v1 skips unknown files, so a new file needs no release order. SERVICE.md: the release order was for `rankings.json` only. | 2 failed | pass |

One GREEN-time fix: the rewrap split ``"1.2"` added `rankings.json`` across lines, which
`test_contract_versioning_says_1_2_added_rankings_json` caught; rewrapped.

Full suite: 1015 passed, 1 skipped.
