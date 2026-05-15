# Releasing OpenKB

This document is for maintainers. Releases are fully automated through
[`.github/workflows/publish.yml`][workflow]: pushing a `v*` tag builds the
package, publishes it to PyPI via OIDC trusted publishing, and creates a
GitHub Release with auto-generated notes.

**Do not run `python -m build && twine upload` from your local machine.**
The workflow is the single source of truth for releases. Manual uploads
bypass the version check, leave no GitHub Release record, and require a
PyPI API token to live on someone's laptop.

## Cutting a release

1. **Update the version** in `pyproject.toml`. Follow [PEP 440][pep440]
   (`0.1.4`, `0.2.0`, `1.0.0rc1`, `1.0.0`).

2. **Update `CHANGELOG.md`**. Move entries from the `## [Unreleased]`
   section into a new `## [X.Y.Z] - YYYY-MM-DD` section. Keep
   `## [Unreleased]` as an empty heading at the top for the next cycle.

3. **Commit** both files on `main` (via PR or directly, per project
   policy):

   ```bash
   git commit -am "chore: bump version to X.Y.Z"
   git push origin main
   ```

4. **Tag and push.** The tag must match the version in `pyproject.toml`
   exactly, prefixed with `v`:

   ```bash
   git tag -a vX.Y.Z -m "Release X.Y.Z"
   git push origin vX.Y.Z
   ```

5. **Watch CI.** The `Publish to PyPI` workflow will:
   - Verify the tag matches `pyproject.toml` (fails fast if not).
   - Build the sdist and wheel.
   - Publish to PyPI via OIDC.
   - Create a GitHub Release with auto-generated notes and the built
     artifacts attached.

6. **Verify**:
   - `pip install --upgrade openkb` pulls the new version.
   - The GitHub Releases page shows the new entry with release notes.

## Fixing a botched release

If the CI run fails after PyPI publish succeeded (e.g., release-creation
step crashes), PyPI keeps the package but the tag still exists locally
and remotely. You can re-run just the release-creation step from the
Actions UI, or manually create the GitHub Release via `gh release create`.

If PyPI publish itself failed (e.g., version conflict), delete the tag,
fix the issue, and re-tag:

```bash
git push --delete origin vX.Y.Z
git tag -d vX.Y.Z
# ... fix pyproject.toml or whatever was wrong ...
git tag -a vX.Y.Z -m "Release X.Y.Z"
git push origin vX.Y.Z
```

PyPI does not allow re-uploading the same version number, so a true
re-publish requires bumping to the next number.

## Pre-release versions

For alphas / betas / RCs, use PEP 440 suffixes:

- `v0.2.0a1`, `v0.2.0b1`, `v0.2.0rc1`

The tag pattern `v*` in [`publish.yml`][workflow] catches all of these.
PyPI treats pre-release versions as such; `pip install openkb` won't pick
them up by default, only `pip install --pre openkb` or `pip install openkb==X.Y.Z`.

## Why OIDC trusted publishing?

PyPI uses OpenID Connect to verify that the publishing job came from this
repository and environment, so no long-lived PyPI API token has to live
anywhere. The `environment: pypi` line in [`publish.yml`][workflow] binds
the trust relationship; the corresponding configuration lives on PyPI's
side. See the [PyPA docs][pypa-oidc] for the full setup.

## Past releases not on git tags

Releases 0.0.1, 0.1.0, 0.1.0.dev1, 0.1.1, 0.1.2, and 0.1.3 were published
directly from a maintainer's laptop via `twine`, before this workflow was
adopted. They exist on PyPI but have no corresponding git tag or GitHub
Release. Starting with the next version, every release will follow the
flow above.

[workflow]: .github/workflows/publish.yml
[pep440]: https://peps.python.org/pep-0440/
[pypa-oidc]: https://docs.pypi.org/trusted-publishers/
