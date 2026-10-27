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

## Review fixes

From the code review of PR #9.

| Finding | Fix | RED (cb3d4d1) | GREEN (c790407) |
|---|---|---|---|
| S1 | `test_v1_enums.py` keys each enum by its path in the def (`ModelInfo.tiers/anyOf/0/propertyNames`), so two enums under one field don't overwrite each other. | 1 failed | pass |
| S2 | The walk covers each whole def (a def that is itself an enum is keyed by its name), and a test checks the schema's enum count equals the pinned count. A synthetic schema tests both cases. | (with S1) | pass |
| N3 | Enum values compare as sets: reordering a `Literal` is no change for a client. | (with S1) | pass |
| S3 | The doc tests import `V1_ENUMS`: CONTRACT.md's enum bullet names every pinned field in backticks and says a new value ships as `/v2/`; API.md's enum sentence names each one and says `/v2/`. | 1 failed (API.md, `tiers`) | pass |
| S5 | API.md's enum list adds the keys of `index.model.tiers`. | (with S3) | pass |
| S4, N1, N2 | SERVICE.md's release order: fetch_v1 "allowlisted" and "refused" (past), cites site PR alexzfe/fight-card-website#12 instead of the branch, and "This holds once site PR #12 is on the site's `main`." replaces the deploy gate (this PR changes no engine behaviour). | 2 failed | pass |

RED: 4 failed, 145 passed (the enum and doc tests). Left alone: N4 (a single-value `Literal` is emitted as
`const`, not `enum`, so it isn't pinned; there is none today).

Full suite: 1030 passed, 1 skipped.
