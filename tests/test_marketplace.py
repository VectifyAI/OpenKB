"""Tests for openkb.skill.marketplace — regenerate <kb>/.claude-plugin/marketplace.json
from <kb>/output/skills/*/SKILL.md."""

from __future__ import annotations

import json
import textwrap

from openkb.skill.marketplace import regenerate_marketplace


def _make_kb(tmp_path):
    (tmp_path / ".openkb").mkdir()
    (tmp_path / ".openkb" / "config.yaml").write_text("model: gpt-4o-mini\n")
    (tmp_path / "output" / "skills").mkdir(parents=True)
    return tmp_path


def _make_skill(kb, name, description):
    d = kb / "output" / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        textwrap.dedent(f"""\
        ---
        name: {name}
        description: {description}
        ---

        # {name}
        """)
    )


def test_regenerate_creates_manifest_with_one_skill(tmp_path):
    kb = _make_kb(tmp_path)
    _make_skill(kb, "karpathy-thinking", "Reason like Karpathy on transformers.")

    regenerate_marketplace(kb)

    manifest = json.loads((kb / ".claude-plugin" / "marketplace.json").read_text())
    assert manifest["plugins"][0]["skills"] == ["./output/skills/karpathy-thinking"]
    # Naming convention is locked: top-level marketplace name is always "vectify".
    assert manifest["name"] == "vectify"


def test_regenerate_lists_multiple_skills_alphabetical(tmp_path):
    kb = _make_kb(tmp_path)
    _make_skill(kb, "zeta-skill", "z")
    _make_skill(kb, "alpha-skill", "a")
    _make_skill(kb, "middle-skill", "m")

    regenerate_marketplace(kb)

    manifest = json.loads((kb / ".claude-plugin" / "marketplace.json").read_text())
    assert manifest["plugins"][0]["skills"] == [
        "./output/skills/alpha-skill",
        "./output/skills/middle-skill",
        "./output/skills/zeta-skill",
    ]


def test_regenerate_replaces_existing_file(tmp_path):
    kb = _make_kb(tmp_path)
    (kb / ".claude-plugin").mkdir()
    (kb / ".claude-plugin" / "marketplace.json").write_text('{"name": "stale"}')

    _make_skill(kb, "demo", "d")
    regenerate_marketplace(kb)

    manifest = json.loads((kb / ".claude-plugin" / "marketplace.json").read_text())
    assert manifest["name"] == "vectify"
    assert manifest["plugins"][0]["skills"] == ["./output/skills/demo"]


def test_regenerate_handles_zero_skills(tmp_path):
    kb = _make_kb(tmp_path)
    regenerate_marketplace(kb)

    manifest = json.loads((kb / ".claude-plugin" / "marketplace.json").read_text())
    assert manifest["plugins"][0]["skills"] == []


def test_regenerate_skips_skill_with_missing_skill_md(tmp_path):
    kb = _make_kb(tmp_path)
    # Folder exists but no SKILL.md — should not appear in manifest
    (kb / "output" / "skills" / "broken").mkdir(parents=True)
    _make_skill(kb, "good", "g")

    regenerate_marketplace(kb)

    manifest = json.loads((kb / ".claude-plugin" / "marketplace.json").read_text())
    assert manifest["plugins"][0]["skills"] == ["./output/skills/good"]


def test_regenerate_reads_description_from_frontmatter(tmp_path):
    kb = _make_kb(tmp_path)
    _make_skill(kb, "demo", "the specific description goes here")
    regenerate_marketplace(kb)

    manifest = json.loads((kb / ".claude-plugin" / "marketplace.json").read_text())
    # Per-skill descriptions live in the SKILL.md frontmatter, NOT in the
    # marketplace manifest. The manifest just points at skill directories;
    # the loading agent (Claude Code, npx skills) reads each SKILL.md to
    # discover the per-skill description. Locking this in: the description
    # must NOT leak into the top-level manifest metadata.
    assert "the specific description goes here" not in manifest["metadata"]["description"]
    # And the description IS preserved on disk in SKILL.md
    skill_md = (kb / "output" / "skills" / "demo" / "SKILL.md").read_text()
    assert "the specific description goes here" in skill_md


def test_regenerate_includes_owner_from_git_config(tmp_path, monkeypatch):
    """The manifest must include an ``owner`` field at the top level so
    that Claude Code's /plugin marketplace add accepts it."""
    kb = _make_kb(tmp_path)
    _make_skill(kb, "demo", "d")

    # Patch subprocess.run to return a controlled git output
    import subprocess

    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["git", "config", "--get"]:
            key = cmd[3]
            if key == "user.name":
                return subprocess.CompletedProcess(cmd, 0, stdout="Test User\n", stderr="")
            if key == "user.email":
                return subprocess.CompletedProcess(cmd, 0, stdout="test@example.com\n", stderr="")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)

    regenerate_marketplace(kb)

    manifest = json.loads((kb / ".claude-plugin" / "marketplace.json").read_text())
    assert manifest["owner"] == {"name": "Test User", "email": "test@example.com"}
    assert manifest["plugins"][0]["author"] == {"name": "Test User", "email": "test@example.com"}


def test_regenerate_falls_back_when_no_git_config(tmp_path, monkeypatch):
    """If git config is empty, manifest still generates with a placeholder."""
    kb = _make_kb(tmp_path)
    _make_skill(kb, "demo", "d")

    import subprocess

    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["git", "config", "--get"]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)

    regenerate_marketplace(kb)

    manifest = json.loads((kb / ".claude-plugin" / "marketplace.json").read_text())
    assert manifest["owner"]["name"] == "openkb-user"
    # email is optional when missing
    assert "email" not in manifest["owner"] or manifest["owner"]["email"] == ""


def test_regenerate_uses_openkb_at_vectify_convention(tmp_path):
    """All OpenKB-generated marketplaces must self-identify as 'vectify'
    (top level) with a plugin named 'openkb', so users install via the
    canonical `openkb@vectify` regardless of which KB they're consuming.
    Different KBs are distinguished by <owner>/<repo> URL, not manifest name."""
    kb = _make_kb(tmp_path)
    _make_skill(kb, "demo", "d")
    regenerate_marketplace(kb)

    manifest = json.loads((kb / ".claude-plugin" / "marketplace.json").read_text())
    assert manifest["name"] == "vectify"
    assert manifest["plugins"][0]["name"] == "openkb"


def test_regenerate_description_is_not_truncated(tmp_path):
    """Manifest description must be a clean fixed string — no truncation
    of SKILL.md content, no '...' mid-word."""
    kb = _make_kb(tmp_path)
    _make_skill(kb, "demo", "the specific description goes here")
    regenerate_marketplace(kb)

    manifest = json.loads((kb / ".claude-plugin" / "marketplace.json").read_text())
    # Must not contain the per-skill description (we don't inject it anymore)
    assert "the specific description goes here" not in manifest["metadata"]["description"]
    # Must not end with a truncated word (no trailing space-letter-letter etc.)
    desc = manifest["metadata"]["description"]
    assert not desc.endswith(" ")
    assert desc.endswith(".") or desc.endswith("OpenKB.")
