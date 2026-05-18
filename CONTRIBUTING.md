# Contributing to OpenKB

Thanks for your interest in OpenKB! There are two ways to contribute: code (bug
fixes, new features) and skills (compiled knowledge artifacts).

## Code contributions

Standard GitHub workflow:

1. Fork the repo and create a feature branch off `main`.
2. Add tests for any new behaviour. Run `uv run pytest` to verify they pass.
3. Open a PR with a clear description and link to any related issues.
4. Address review feedback. A maintainer will merge once everything looks good.

## Submitting a skill

OpenKB doubles as a registry for compiled-knowledge skills. The `skills/`
directory in this repo hosts the official `openkb` skill plus
community-contributed skills installable via:

```bash
npx skills@latest add VectifyAI/OpenKB
```

To submit your skill:

### 1. Compile and test locally

```bash
cd <your-kb>
openkb skill new <skill-name> "<one-sentence intent>"
cp -r output/skills/<skill-name> ~/.claude/skills/
# Verify the skill activates in a Claude Code session on a relevant question
```

### 2. Open a PR against this repo

1. Fork `VectifyAI/OpenKB`.
2. Copy your skill into the fork at `skills/<your-skill-name>/`.
3. Add a new entry under `plugins[0].skills` in `.claude-plugin/marketplace.json`:
   ```json
   "./skills/<your-skill-name>"
   ```
4. Open a PR with the "Skill submission" template (auto-applied when you create
   the PR).

### 3. Review

A maintainer will review your submission against this checklist:

- `SKILL.md` has a valid YAML frontmatter with `name:` and `description:`
  (description ≤ 1024 characters).
- The `description` is specific (not "a skill about X").
- All `[[references/...]]` links resolve to files you actually included.
- Skill name doesn't clash with an existing entry.
- No obvious copyright-infringing content (large verbatim copies of recent
  copyrighted books or papers without authorisation).

You're responsible for confirming you have rights to redistribute the source
material your skill is derived from. The PR template includes a checkbox for
this.

### What makes a good community skill

- **Methodology-focused**, not bulk-copied content. "How to reason about X"
  beats "the full text of X".
- **Specific scope** so the `description:` field can be precise enough that
  loading agents pick it up at the right time.
- **Tested in at least one agent CLI** before submission.

Thanks for sharing what you've compiled!
