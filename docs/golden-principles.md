# Golden Principles

Opinionated, mechanical rules that keep this agent-generated codebase legible
and consistent for future agent runs. Enforced by CI where possible; the rest
are honored by convention and checked in review. When a rule proves valuable,
promote it into a lint (see `tests/test_file_size.py` for the pattern).

## Boundaries
- **Validate data shapes at boundaries.** Parse/validate inputs (frontmatter via
  `openkb/frontmatter.py`, config via `openkb/config.py`) at the edge. Never build
  on guessed shapes.

## Reuse
- **Prefer shared utilities over hand-rolled helpers** so invariants stay
  centralized. Check `openkb/` for an existing helper before writing a new one.

## Temporal claims
- **Keep claim history.** OpenKB appends new claim records. OpenKB controls the
  `id` field and the `superseded_by` supersession links. OpenKB does not define
  the format of the `source_anchor` field.
- **Use strict current reads.** By default, return only claims that have a
  `status` value of `validated`. Return claims that have a `status` value of
  `proposed` only after an explicit request. A claim that has a `status` value
  of `proposed` cannot supersede a claim that has a `status` value of
  `validated`. A claim that has a `status` value of `validated` or `abandoned`
  can supersede a claim that has a `status` value of `validated` when all other
  rules permit the supersession link.
- **Limit assistant authority.** If an assistant claim has a `status` value of
  `validated`, change the value to `proposed`. An assistant claim cannot enter
  strict current reads. An assistant claim cannot supersede a claim that has a
  `status` value of `validated`. Only a claim with an `authority` value of
  `first_party` can supersede another claim with an `authority` value of
  `first_party`. Claims with an `authority` value of `document` use the same
  `as_of` and `status` rules as claims without an `authority` field.
- **Ignore duplicate claim input.** If a stored claim has the same `id` value as
  an incoming duplicate claim, keep the stored claim. Ignore every field and
  every supersession link from the incoming duplicate claim.
- **Limit compiler authority.** For each claim from a document compiler model,
  set the `authority` field to `document`. An external adapter can verify source
  authority. The adapter can then set the `authority` field to `first_party` or
  `assistant` through the public claims API.

## I/O and state
- **All wiki file writes go through `openkb/locks.py` / `openkb/mutation.py`**
  (atomic, crash-safe). No ad-hoc writes to the wiki tree.
- **Log through `openkb/log.py`**, not bare `print`, for anything diagnostic.

## Size and shape
<a id="file-size"></a>
- **Keep modules focused and under 800 lines** (enforced by
  `tests/test_file_size.py`). Split large modules into focused units by
  responsibility. Existing over-limit files are grandfathered (with reasons)
  in the test's `_GRANDFATHERED` set and additionally tracked in
  `docs/internal/tech-debt.md` *(maintainer-local, not in git)*.

## Docs
- **`AGENTS.md` is a map, not a manual.** Keep it short; deep/local docs live
  under `docs/` (public) and `docs/internal/` (maintainer-local, not in git).
