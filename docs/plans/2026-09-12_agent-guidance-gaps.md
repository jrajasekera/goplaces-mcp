# Agent guidance gaps in the goplaces plugin

Date: 2026-09-12
Status: implemented on branch `agent-guidance-gaps`

Outcome: D1 accepted (directions now defaults to drive), D2 accepted as
recommended (`detail_level` keeps its `full` default; the skill's cost section
became prescriptive instead). Phase 3's consistency check went into
`tests/test_packaging.py`, whose stated job is already guarding duplicated
facts, rather than a new `tests/test_skill.py`. The `claude plugin eval` suite
remains a follow-up.

## Problem

The plugin already has three guidance layers — per-parameter schema descriptions
in `src/goplaces_mcp/schemas.py`, `SERVER_INSTRUCTIONS` sent at initialize, and
`skills/goplaces/SKILL.md`. They are good, and a fourth layer is not the fix.
What is missing is coverage of the specific places an agent goes wrong, plus any
test that the guidance stays true as the code changes.

Seven concrete gaps, in priority order:

1. **`goplaces_directions` defaults to `mode: "walk"`** (`schemas.py:418`,
   `tools.py:790`, `tools.py:1572`). Nothing in the skill or the server
   instructions says so. "How far is the airport from the hotel" silently
   returns a walking route that looks plausible and is wrong. This is the only
   gap that produces a confidently wrong answer rather than a costlier or
   clumsier one.
2. **The cost advice contradicts the default.** `detail_level` defaults to
   `"full"` (`schemas.py:34`) while the skill says prefer `"basic"`. An agent
   reading the schema and not the skill pays the top tier on every call.
3. **`waypoints` accepts text only, not place IDs** (`schemas.py:426`), which
   contradicts the skill's "hand the place ID forward" workflow.
4. **`page_token` requires every other argument to match** — including
   `detail_level`. Documented in the schema, absent from the skill's paging
   section, and a classic agent error.
5. **No worked examples.** The skill is entirely prose rules. Agents follow
   traces better than rules.
6. **No recovery guidance** for zero results, and nothing about what to do with
   a `goplaces_photo` result (the server lifts the bytes into an image block).
7. **No test touches the skill.** `grep -rn SKILL tests/` is empty.
   `SERVER_INSTRUCTIONS` and `SKILL.md` restate the same tool-selection list
   with nothing keeping them in sync, and nothing catches a tool added to the
   registry but not to either document.

## Decisions needed before implementation

- **D1 — change the directions default from `walk` to `drive`?**
  Recommended: yes. `walk` is surprising for an unqualified distance question,
  and `goplaces_route_search` and `goplaces_route_matrix` already default to
  `drive`, so the plugin is internally inconsistent. It is a behavior change but
  not a compatibility-invariant break (those cover tool names, not defaults).
  Fallback if rejected: leave the default and state it in the schema
  description, the skill, and `SERVER_INSTRUCTIONS`.
- **D2 — change the `detail_level` default from `full` to `basic`?**
  Recommended: **no**. A cheap tier that omits hours and ratings usually forces
  a second billable call, so the savings are illusory, and `_strip_none` makes
  the omission indistinguishable from "the place has none". Instead make the
  skill's cost section prescriptive about when to downgrade rather than a
  general preference.

## Plan

### Phase 1 — Code (only if D1 is approved)

1. `schemas.py`: `goplaces_directions.mode` default `walk` → `drive`, and say
   the default in the description.
2. `tools.py`: the two `"walk"` fallbacks (`_as_str(args, "mode", "walk")` at
   line 790 and `_normalize_direction_mode`'s empty-string branch at line 1572)
   become `"drive"`. Keep the fallback in one place if the second is redundant.
3. Tests: add a case asserting an omitted `mode` produces a `DRIVE` request to
   Google via the `google` fixture. Existing tests pass `mode` explicitly, so
   none should need changing — verify that rather than assume it.
4. Update `README.md` and `SERVER_INSTRUCTIONS` wherever the walk default is
   implied.

### Phase 2 — Skill rewrite

Rewrite `skills/goplaces/SKILL.md` keeping its current structure and adding:

- A **Defaults that surprise** section: the directions mode default,
  `detail_level: "full"`, and route-search vs directions mode differences.
- A **Worked examples** section with two or three end-to-end traces showing real
  argument payloads and what to carry forward from each response:
  - "coffee near the Space Needle" → `resolve` → `nearby` (with the returned
    `location`) → `details` for the chosen one.
  - "which of these three offices is closest to home" → single `route_matrix`
    call, not a loop of `directions`.
  - "EV charging on the way to Portland" → `route_search` with
    `ev_connector_types`, reading `detour_seconds` against the `route` object.
- Paging: restate that every argument, `detail_level` included, must match the
  original request when passing `page_token`.
- Waypoints: text only, not place IDs.
- **When a call comes back empty**: widen `radius_m`, drop `min_rating` and
  `open_now` before widening further, and fall back from `nearby` to `search`
  when the area is sparse. Say explicitly that zero results is a real answer,
  not a reason to retry the same call.
- `goplaces_photo`: what the returned image block is and when it is worth
  fetching at all.
- Sharpen the cost section per D2 into "downgrade to `basic` when X, to `ids`
  when Y" rather than a general preference.

### Phase 3 — Keep the guidance honest

1. Add `tests/test_skill.py` asserting that every name in the tool registry
   appears in both `SKILL.md` and `SERVER_INSTRUCTIONS`, and that neither
   mentions a tool that does not exist. This makes the existing
   "adding a tool requires updating the skill" invariant in `AGENTS.md`
   enforced rather than aspirational.
2. Consider a `claude plugin eval` suite with a handful of prompts whose correct
   tool choice is unambiguous ("how long to drive to X" → directions in drive
   mode; "which is closest" → route_matrix; "coffee between A and B" →
   route_search). Scope this as a follow-up if it grows past an hour — the
   test in step 1 is the part that must land with this change.

## Verification

Per `AGENTS.md`, since this touches schemas and packaging:

```sh
uv sync --dev
uv run pytest
uv run python -m compileall -q src tests
uv build
git diff --check
claude plugin validate .
uv run --with pyyaml python ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/goplaces
uv run --with pyyaml python ~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py .
```

No live Google calls. Everything goes through `tests/fake_google.py`.

## Out of scope

- Adding or renaming tools.
- Changing field masks or tiering logic in `_place_field_mask`.
- A second skill. The gaps are coverage gaps in the existing one.
