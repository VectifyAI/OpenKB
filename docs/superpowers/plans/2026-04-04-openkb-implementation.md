# OpenKB Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `okb` CLI that implements Karpathy's LLM Knowledge Base workflow, powered by PageIndex for long documents and markitdown for format conversion.

**Architecture:** A Python CLI (`click`) orchestrates a pipeline: files are converted to markdown (markitdown/PageIndex), then an LLM agent (OpenAI Agents SDK) compiles summaries and cross-document concepts into a wiki of `.md` files. Q&A, watch mode, and linting operate on this wiki.

**Tech Stack:** Python 3.10+, click, markitdown, pageindex (git dep), openai-agents, litellm, watchdog, pyyaml

---

## File Structure

```
openkb/
├── __init__.py              # Package version
├── cli.py                   # Click CLI commands (init, add, query, watch, lint, list, status)
├── config.py                # Load/save .okb/config.yaml, defaults
├── state.py                 # .okb/hashes.json management, document metadata
├── converter.py             # markitdown conversion + dispatch (short vs long)
├── images.py                # Base64 extraction + relative image copying
├── indexer.py               # PageIndex integration (index long PDFs, tree retrieval)
├── tree_renderer.py         # PageIndex tree JSON → markdown (sources/ and summaries/)
├── schema.py                # SCHEMA.md content as a constant string
├── agent/
│   ├── __init__.py
│   ├── tools.py             # Agent function tools (list/read/write wiki, get_page_content)
│   ├── compiler.py          # Wiki compilation agent (summary + concepts in one session)
│   ├── query.py             # Q&A agent + pageindex_retrieve wrapper
│   └── linter.py            # Knowledge lint agent (contradictions, gaps, staleness)
├── lint.py                  # Structural lint checks (broken links, orphans, index sync)
└── watcher.py               # Watch mode (watchdog + debounce)

tests/
├── conftest.py              # Shared fixtures (tmp knowledge base dirs, mock config)
├── test_config.py
├── test_state.py
├── test_images.py
├── test_converter.py
├── test_tree_renderer.py
├── test_indexer.py
├── test_agent_tools.py
├── test_compiler.py
├── test_query.py
├── test_lint.py
├── test_watcher.py
├── test_cli.py
└── fixtures/
    ├── short.pdf             # A 2-page PDF for testing
    ├── sample.md             # A markdown file with relative images
    ├── sample_image.png      # Referenced by sample.md
    └── tree_structure.json   # Sample PageIndex tree output
```

---

## Task 1: Project Scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `openkb/__init__.py`
- Create: `openkb/cli.py`
- Create: `tests/conftest.py`
- Create: `.gitignore`

- [ ] **Step 1: Create pyproject.toml**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "openkb"
version = "0.1.0"
description = "Karpathy's LLM Knowledge Base workflow — powered by PageIndex"
readme = "README.md"
requires-python = ">=3.10"
license = "MIT"
dependencies = [
    "pageindex @ git+https://github.com/KylinMountain/PageIndex.git@feat/sdk",
    "markitdown",
    "click>=8.0",
    "watchdog>=3.0",
    "litellm",
    "openai-agents",
    "pyyaml",
]

[project.scripts]
okb = "openkb.cli:cli"

[tool.pytest.ini_options]
testpaths = ["tests"]

[project.optional-dependencies]
dev = ["pytest", "pytest-tmp-files"]
```

- [ ] **Step 2: Create .gitignore**

```
__pycache__/
*.pyc
*.egg-info/
dist/
build/
.venv/
venv/
*.db
.DS_Store
.env
```

- [ ] **Step 3: Create openkb/__init__.py**

```python
__version__ = "0.1.0"
```

- [ ] **Step 4: Create CLI skeleton in openkb/cli.py**

```python
import click


@click.group()
def cli():
    """OpenKB — Karpathy's LLM Knowledge Base workflow, powered by PageIndex."""
    pass


@cli.command()
def init():
    """Initialize a new knowledge base."""
    click.echo("Not implemented yet.")


@cli.command()
@click.argument("path", type=click.Path(exists=True))
def add(path):
    """Add a document or directory to the knowledge base."""
    click.echo("Not implemented yet.")


@cli.command()
@click.argument("question")
def query(question):
    """Ask a question against the knowledge base."""
    click.echo("Not implemented yet.")


@cli.command()
def watch():
    """Watch raw/ directory and auto-compile new files."""
    click.echo("Not implemented yet.")


@cli.command()
@click.option("--fix", is_flag=True, help="Auto-fix issues where possible.")
def lint(fix):
    """Run health checks on the wiki."""
    click.echo("Not implemented yet.")


@cli.command(name="list")
def list_docs():
    """List indexed documents."""
    click.echo("Not implemented yet.")


@cli.command()
def status():
    """Show knowledge base status."""
    click.echo("Not implemented yet.")
```

- [ ] **Step 5: Create tests/conftest.py with shared fixtures**

```python
import os
import json
from pathlib import Path

import pytest


@pytest.fixture
def kb_dir(tmp_path):
    """Create a minimal knowledge base directory structure."""
    raw = tmp_path / "raw"
    raw.mkdir()
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "sources").mkdir()
    (wiki / "sources" / "images").mkdir()
    (wiki / "summaries").mkdir()
    (wiki / "concepts").mkdir()
    (wiki / "reports").mkdir()
    okb = tmp_path / ".okb"
    okb.mkdir()
    (okb / "config.yaml").write_text(
        "model: gpt-4o\napi_key_env: OPENAI_API_KEY\nlanguage: en\npageindex_threshold: 50\n"
    )
    (okb / "hashes.json").write_text("{}")
    return tmp_path


@pytest.fixture
def sample_tree():
    """Sample PageIndex tree structure for testing."""
    return {
        "doc_name": "test.pdf",
        "doc_description": "A test document about attention mechanisms.",
        "structure": [
            {
                "title": "Introduction",
                "node_id": "0000",
                "start_index": 1,
                "end_index": 5,
                "summary": "Introduces the concept of attention in neural networks.",
                "text": "Attention is a mechanism that allows models to focus...",
                "nodes": [
                    {
                        "title": "Background",
                        "node_id": "0001",
                        "start_index": 1,
                        "end_index": 2,
                        "summary": "Background on sequence-to-sequence models.",
                        "text": "Sequence-to-sequence models were first proposed...",
                    }
                ],
            },
            {
                "title": "Methods",
                "node_id": "0002",
                "start_index": 6,
                "end_index": 15,
                "summary": "Describes self-attention and multi-head attention.",
                "text": "Self-attention computes attention weights...",
            },
        ],
    }
```

- [ ] **Step 6: Verify CLI entry point works**

Run: `pip install -e ".[dev]" && okb --help`
Expected: Help text showing all commands (init, add, query, watch, lint, list, status).

- [ ] **Step 7: Run tests (empty suite, should pass)**

Run: `pytest -v`
Expected: `no tests ran` or collected 0 items. No errors.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml .gitignore openkb/ tests/
git commit -m "feat: project scaffold with CLI skeleton and test fixtures"
```

---

## Task 2: Config and State Management

**Files:**
- Create: `openkb/config.py`
- Create: `openkb/state.py`
- Create: `openkb/schema.py`
- Create: `tests/test_config.py`
- Create: `tests/test_state.py`

- [ ] **Step 1: Write tests for config**

```python
# tests/test_config.py
from pathlib import Path

from openkb.config import load_config, save_config, DEFAULT_CONFIG


def test_default_config():
    assert DEFAULT_CONFIG["model"] == "gpt-4o"
    assert DEFAULT_CONFIG["language"] == "en"
    assert DEFAULT_CONFIG["pageindex_threshold"] == 50


def test_save_and_load_config(tmp_path):
    config_path = tmp_path / ".okb" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    save_config(config_path, DEFAULT_CONFIG)
    loaded = load_config(config_path)
    assert loaded == DEFAULT_CONFIG


def test_load_config_missing_file(tmp_path):
    config_path = tmp_path / ".okb" / "config.yaml"
    loaded = load_config(config_path)
    assert loaded == DEFAULT_CONFIG
```

- [ ] **Step 2: Run tests — should fail**

Run: `pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'openkb.config'`

- [ ] **Step 3: Implement config.py**

```python
# openkb/config.py
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG: dict[str, Any] = {
    "model": "gpt-4o",
    "api_key_env": "OPENAI_API_KEY",
    "language": "en",
    "pageindex_threshold": 50,
}


def load_config(config_path: Path) -> dict[str, Any]:
    """Load config from yaml file, falling back to defaults."""
    if config_path.exists():
        with open(config_path) as f:
            user_config = yaml.safe_load(f) or {}
        return {**DEFAULT_CONFIG, **user_config}
    return dict(DEFAULT_CONFIG)


def save_config(config_path: Path, config: dict[str, Any]) -> None:
    """Save config to yaml file."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
```

- [ ] **Step 4: Run config tests — should pass**

Run: `pytest tests/test_config.py -v`
Expected: 3 passed.

- [ ] **Step 5: Write tests for state**

```python
# tests/test_state.py
from pathlib import Path

from openkb.state import HashRegistry


def test_empty_registry(tmp_path):
    reg = HashRegistry(tmp_path / "hashes.json")
    assert not reg.is_known("abc123")


def test_add_and_check(tmp_path):
    reg = HashRegistry(tmp_path / "hashes.json")
    reg.add("abc123", {"name": "paper.pdf", "type": "short"})
    assert reg.is_known("abc123")
    assert reg.get("abc123")["name"] == "paper.pdf"


def test_persistence(tmp_path):
    path = tmp_path / "hashes.json"
    reg1 = HashRegistry(path)
    reg1.add("abc123", {"name": "paper.pdf", "type": "short"})

    reg2 = HashRegistry(path)
    assert reg2.is_known("abc123")


def test_hash_file(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("hello world")
    h = HashRegistry.hash_file(f)
    assert len(h) == 64  # SHA-256 hex digest


def test_all_entries(tmp_path):
    reg = HashRegistry(tmp_path / "hashes.json")
    reg.add("aaa", {"name": "a.pdf", "type": "short"})
    reg.add("bbb", {"name": "b.pdf", "type": "pageindex"})
    entries = reg.all_entries()
    assert len(entries) == 2
```

- [ ] **Step 6: Run state tests — should fail**

Run: `pytest tests/test_state.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 7: Implement state.py**

```python
# openkb/state.py
import hashlib
import json
from pathlib import Path
from typing import Any


class HashRegistry:
    """Tracks processed file hashes and metadata in .okb/hashes.json."""

    def __init__(self, path: Path):
        self._path = path
        self._data: dict[str, dict[str, Any]] = {}
        if path.exists():
            with open(path) as f:
                self._data = json.load(f)

    def is_known(self, file_hash: str) -> bool:
        return file_hash in self._data

    def get(self, file_hash: str) -> dict[str, Any] | None:
        return self._data.get(file_hash)

    def add(self, file_hash: str, metadata: dict[str, Any]) -> None:
        self._data[file_hash] = metadata
        self._save()

    def all_entries(self) -> dict[str, dict[str, Any]]:
        return dict(self._data)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w") as f:
            json.dump(self._data, f, indent=2)

    @staticmethod
    def hash_file(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
```

- [ ] **Step 8: Run state tests — should pass**

Run: `pytest tests/test_state.py -v`
Expected: 5 passed.

- [ ] **Step 9: Create schema.py**

```python
# openkb/schema.py
SCHEMA_MD = """\
# Wiki Schema

## Directory Structure
- sources/ — Full-text converted from raw documents. Do not modify directly.
- sources/images/ — Extracted images from documents, referenced by sources.
- summaries/ — One per source document. Summary of key content.
- concepts/ — Cross-document topic synthesis. Created when a theme spans multiple documents.
- reports/ — Lint health check reports. Auto-generated.

## Page Types
- **Summary Page** (summaries/): Key content of a single source document.
- **Concept Page** (concepts/): Cross-document topic synthesis with [[wikilinks]].
- **Index Page** (index.md): One-liner summary of every page in the wiki. Auto-maintained.

## Index Page Format
index.md lists all documents and concepts with metadata:
- Documents: name, one-liner description, type (short|pageindex), detail access path
- Concepts: name, one-liner description

## Format
- Use [[wikilink]] to link other wiki pages (e.g., [[concepts/attention]])
- Summary pages header: `sources: [paper.pdf]`
- Concept pages header: `sources: [paper1.pdf, paper2.pdf, ...]`
- Standard Markdown heading hierarchy
- Keep each page focused on a single topic
"""
```

- [ ] **Step 10: Commit**

```bash
git add openkb/config.py openkb/state.py openkb/schema.py tests/test_config.py tests/test_state.py
git commit -m "feat: config, state management, and SCHEMA.md constant"
```

---

## Task 3: okb init Command

**Files:**
- Modify: `openkb/cli.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write test for okb init**

```python
# tests/test_cli.py
from pathlib import Path

from click.testing import CliRunner

from openkb.cli import cli


def test_init_creates_structure(tmp_path):
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path) as td:
        result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0
        base = Path(td)
        assert (base / "raw").is_dir()
        assert (base / "wiki" / "sources" / "images").is_dir()
        assert (base / "wiki" / "summaries").is_dir()
        assert (base / "wiki" / "concepts").is_dir()
        assert (base / "wiki" / "reports").is_dir()
        assert (base / "wiki" / "SCHEMA.md").exists()
        assert (base / "wiki" / "index.md").exists()
        assert (base / ".okb" / "config.yaml").exists()
        assert (base / ".okb" / "hashes.json").exists()


def test_init_schema_content(tmp_path):
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path) as td:
        runner.invoke(cli, ["init"])
        schema = (Path(td) / "wiki" / "SCHEMA.md").read_text()
        assert "Wiki Schema" in schema
        assert "[[wikilink]]" in schema


def test_init_already_exists(tmp_path):
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path) as td:
        runner.invoke(cli, ["init"])
        result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0
        assert "already initialized" in result.output.lower()
```

- [ ] **Step 2: Run tests — should fail**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL — init still prints "Not implemented yet."

- [ ] **Step 3: Implement okb init**

```python
# Replace the init function in openkb/cli.py
import json
from pathlib import Path

from openkb.config import save_config, DEFAULT_CONFIG
from openkb.schema import SCHEMA_MD


@cli.command()
def init():
    """Initialize a new knowledge base."""
    base = Path.cwd()
    okb_dir = base / ".okb"

    if okb_dir.exists():
        click.echo("Knowledge base already initialized.")
        return

    # Create directory structure
    (base / "raw").mkdir(exist_ok=True)
    for sub in ["sources/images", "summaries", "concepts", "reports"]:
        (base / "wiki" / sub).mkdir(parents=True, exist_ok=True)

    # Write SCHEMA.md
    (base / "wiki" / "SCHEMA.md").write_text(SCHEMA_MD)

    # Write empty index.md
    (base / "wiki" / "index.md").write_text(
        "# Knowledge Base Index\n\n## Documents\n\n## Concepts\n"
    )

    # Write config and empty hash registry
    okb_dir.mkdir(parents=True)
    save_config(okb_dir / "config.yaml", DEFAULT_CONFIG)
    (okb_dir / "hashes.json").write_text("{}")

    click.echo("Initialized knowledge base in current directory.")
```

- [ ] **Step 4: Run tests — should pass**

Run: `pytest tests/test_cli.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add openkb/cli.py tests/test_cli.py
git commit -m "feat: implement okb init command"
```

---

## Task 4: Image Extraction

**Files:**
- Create: `openkb/images.py`
- Create: `tests/test_images.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_images.py
import base64
from pathlib import Path

from openkb.images import extract_base64_images, copy_relative_images


def _make_b64_png():
    """Create a minimal 1x1 PNG as base64."""
    # Minimal valid PNG (1x1 red pixel)
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
        b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
        b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    return base64.b64encode(png_bytes).decode()


def test_extract_base64_images(tmp_path):
    b64 = _make_b64_png()
    md = f"Hello\n![fig](data:image/png;base64,{b64})\nWorld"
    images_dir = tmp_path / "images" / "doc1"

    result = extract_base64_images(md, "doc1", images_dir)

    assert "data:image" not in result
    assert "![fig](images/doc1/img_001.png)" in result
    assert (images_dir / "img_001.png").exists()
    assert (images_dir / "img_001.png").stat().st_size > 0


def test_extract_multiple_images(tmp_path):
    b64 = _make_b64_png()
    md = f"![a](data:image/png;base64,{b64})\n![b](data:image/jpeg;base64,{b64})"
    images_dir = tmp_path / "images" / "doc2"

    result = extract_base64_images(md, "doc2", images_dir)

    assert "img_001.png" in result
    assert "img_002.jpeg" in result


def test_no_images(tmp_path):
    md = "Just text, no images."
    result = extract_base64_images(md, "doc", tmp_path / "images" / "doc")
    assert result == md


def test_copy_relative_images(tmp_path):
    # Set up source directory with image
    src_dir = tmp_path / "source"
    src_dir.mkdir()
    img_dir = src_dir / "imgs"
    img_dir.mkdir()
    (img_dir / "fig1.png").write_bytes(b"fake png data")
    md_content = "Text\n![Figure 1](imgs/fig1.png)\nMore text"

    dest_images = tmp_path / "wiki" / "sources" / "images" / "notes"
    result = copy_relative_images(md_content, src_dir, "notes", dest_images)

    assert "![Figure 1](images/notes/fig1.png)" in result
    assert (dest_images / "fig1.png").exists()


def test_copy_relative_images_missing_file(tmp_path):
    src_dir = tmp_path / "source"
    src_dir.mkdir()
    md_content = "![Missing](nonexistent.png)"

    dest_images = tmp_path / "wiki" / "sources" / "images" / "doc"
    result = copy_relative_images(md_content, src_dir, "doc", dest_images)

    # Missing images are left as-is
    assert "![Missing](nonexistent.png)" in result
```

- [ ] **Step 2: Run tests — should fail**

Run: `pytest tests/test_images.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement images.py**

```python
# openkb/images.py
import base64
import logging
import re
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

_BASE64_PATTERN = re.compile(
    r"!\[([^\]]*)\]\(data:image/([^;]+);base64,([^)]+)\)"
)
_RELATIVE_IMG_PATTERN = re.compile(
    r"!\[([^\]]*)\]\((?!https?://|data:)([^)]+)\)"
)


def extract_base64_images(
    markdown: str, doc_name: str, images_dir: Path
) -> str:
    """Replace base64 data URIs with local image files."""
    counter = 0

    def _replace(match: re.Match) -> str:
        nonlocal counter
        alt, mime_sub, data = match.group(1), match.group(2), match.group(3)
        counter += 1
        filename = f"img_{counter:03d}.{mime_sub}"
        images_dir.mkdir(parents=True, exist_ok=True)
        try:
            (images_dir / filename).write_bytes(base64.b64decode(data))
        except Exception:
            logger.warning("Failed to decode base64 image %d in %s", counter, doc_name)
            return match.group(0)
        return f"![{alt}](images/{doc_name}/{filename})"

    return _BASE64_PATTERN.sub(_replace, markdown)


def copy_relative_images(
    markdown: str, source_dir: Path, doc_name: str, images_dir: Path
) -> str:
    """Copy relatively-referenced images and rewrite paths."""

    def _replace(match: re.Match) -> str:
        alt, rel_path = match.group(1), match.group(2)
        src_file = (source_dir / rel_path).resolve()
        if not src_file.exists():
            logger.warning("Image not found: %s", src_file)
            return match.group(0)
        images_dir.mkdir(parents=True, exist_ok=True)
        dest = images_dir / src_file.name
        shutil.copy2(src_file, dest)
        return f"![{alt}](images/{doc_name}/{src_file.name})"

    return _RELATIVE_IMG_PATTERN.sub(_replace, markdown)
```

- [ ] **Step 4: Run tests — should pass**

Run: `pytest tests/test_images.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add openkb/images.py tests/test_images.py
git commit -m "feat: image extraction from base64 and relative paths"
```

---

## Task 5: Converter Module

**Files:**
- Create: `openkb/converter.py`
- Create: `tests/test_converter.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_converter.py
from pathlib import Path
from unittest.mock import patch, MagicMock

from openkb.converter import convert_document, get_pdf_page_count


def test_convert_markdown_input(kb_dir):
    # Create a .md source file
    src = kb_dir / "test_input.md"
    src.write_text("# Hello\n\nSome content about transformers.\n")

    result = convert_document(src, kb_dir)

    assert result.source_path == kb_dir / "wiki" / "sources" / "test_input.md"
    assert result.source_path.exists()
    assert "Hello" in result.source_path.read_text()
    assert result.is_long_doc is False
    assert (kb_dir / "raw" / "test_input.md").exists()


def test_convert_markdown_with_images(kb_dir):
    # Create source dir with image
    src_dir = kb_dir / "external"
    src_dir.mkdir()
    (src_dir / "fig.png").write_bytes(b"fake png")
    src = src_dir / "notes.md"
    src.write_text("![diagram](fig.png)\n")

    result = convert_document(src, kb_dir)

    content = result.source_path.read_text()
    assert "images/notes/fig.png" in content
    assert (kb_dir / "wiki" / "sources" / "images" / "notes" / "fig.png").exists()


@patch("openkb.converter.get_pdf_page_count", return_value=10)
@patch("openkb.converter.MarkItDown")
def test_convert_short_pdf(mock_md_cls, mock_pages, kb_dir):
    mock_md = MagicMock()
    mock_md.convert.return_value = MagicMock(text_content="# Paper\nContent here.")
    mock_md_cls.return_value = mock_md

    src = kb_dir / "paper.pdf"
    src.write_bytes(b"%PDF-fake")

    result = convert_document(src, kb_dir)

    assert result.is_long_doc is False
    assert result.source_path.exists()
    assert "Paper" in result.source_path.read_text()


@patch("openkb.converter.get_pdf_page_count", return_value=100)
def test_convert_long_pdf_detected(mock_pages, kb_dir):
    src = kb_dir / "textbook.pdf"
    src.write_bytes(b"%PDF-fake")

    result = convert_document(src, kb_dir)

    assert result.is_long_doc is True
    assert result.source_path is None  # Not converted by markitdown


def test_convert_duplicate_skipped(kb_dir):
    src = kb_dir / "test.md"
    src.write_text("content")

    result1 = convert_document(src, kb_dir)
    assert result1.skipped is False

    result2 = convert_document(src, kb_dir)
    assert result2.skipped is True
```

- [ ] **Step 2: Run tests — should fail**

Run: `pytest tests/test_converter.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement converter.py**

```python
# openkb/converter.py
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from openkb.config import load_config
from openkb.images import copy_relative_images, extract_base64_images
from openkb.state import HashRegistry

logger = logging.getLogger(__name__)


@dataclass
class ConvertResult:
    raw_path: Path | None  # Path in raw/
    source_path: Path | None  # Path in wiki/sources/
    is_long_doc: bool  # True if PageIndex should handle this
    skipped: bool  # True if duplicate


def get_pdf_page_count(path: Path) -> int:
    """Get page count of a PDF file."""
    import pymupdf

    with pymupdf.open(str(path)) as doc:
        return len(doc)


def convert_document(src: Path, kb_dir: Path) -> ConvertResult:
    """Convert a document to wiki/sources/ markdown. Returns metadata."""
    config = load_config(kb_dir / ".okb" / "config.yaml")
    registry = HashRegistry(kb_dir / ".okb" / "hashes.json")

    # Dedup check
    file_hash = HashRegistry.hash_file(src)
    if registry.is_known(file_hash):
        logger.info("Skipping duplicate: %s", src.name)
        return ConvertResult(
            raw_path=None, source_path=None, is_long_doc=False, skipped=True
        )

    # Copy to raw/
    raw_path = kb_dir / "raw" / src.name
    if not raw_path.exists():
        shutil.copy2(src, raw_path)

    doc_stem = src.stem
    sources_dir = kb_dir / "wiki" / "sources"
    images_dir = sources_dir / "images" / doc_stem

    # Check if long PDF
    threshold = config.get("pageindex_threshold", 50)
    if src.suffix.lower() == ".pdf":
        page_count = get_pdf_page_count(src)
        if page_count >= threshold:
            registry.add(
                file_hash,
                {"name": src.name, "type": "pageindex", "pages": page_count},
            )
            return ConvertResult(
                raw_path=raw_path, source_path=None, is_long_doc=True, skipped=False
            )

    # Convert to markdown
    if src.suffix.lower() == ".md":
        md_content = src.read_text(encoding="utf-8")
        md_content = copy_relative_images(
            md_content, src.parent, doc_stem, images_dir
        )
    else:
        from markitdown import MarkItDown

        converter = MarkItDown()
        result = converter.convert(str(src))
        md_content = result.text_content
        md_content = extract_base64_images(md_content, doc_stem, images_dir)

    source_path = sources_dir / f"{doc_stem}.md"
    source_path.write_text(md_content, encoding="utf-8")

    registry.add(file_hash, {"name": src.name, "type": "short"})

    return ConvertResult(
        raw_path=raw_path, source_path=source_path, is_long_doc=False, skipped=False
    )
```

- [ ] **Step 4: Run tests — should pass**

Run: `pytest tests/test_converter.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add openkb/converter.py tests/test_converter.py
git commit -m "feat: document converter with markitdown and dedup"
```

---

## Task 6: PageIndex Tree Renderer

**Files:**
- Create: `openkb/tree_renderer.py`
- Create: `tests/test_tree_renderer.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_tree_renderer.py
from openkb.tree_renderer import render_source_md, render_summary_md


def test_render_source_md(sample_tree):
    result = render_source_md(sample_tree, "test.pdf", "doc-001")

    assert "---" in result  # frontmatter
    assert "source: test.pdf" in result
    assert "type: pageindex" in result
    assert "doc_id: doc-001" in result
    assert "# Introduction" in result
    assert "(pages 1" in result
    assert "Attention is a mechanism" in result
    assert "## Background" in result
    assert "Sequence-to-sequence" in result
    assert "# Methods" in result


def test_render_summary_md(sample_tree):
    result = render_summary_md(sample_tree, "test.pdf", "doc-001")

    assert "source: test.pdf" in result
    assert "type: pageindex" in result
    assert "# Introduction" in result
    assert "Summary: Introduces the concept" in result
    assert "## Background" in result
    assert "Summary: Background on sequence" in result
    # Should NOT contain full text
    assert "Attention is a mechanism" not in result


def test_render_empty_tree():
    tree = {"doc_name": "empty.pdf", "doc_description": "Empty.", "structure": []}
    result = render_source_md(tree, "empty.pdf", "doc-x")
    assert "source: empty.pdf" in result


def test_render_deep_nesting(sample_tree):
    # Add a third level of nesting
    sample_tree["structure"][0]["nodes"][0]["nodes"] = [
        {
            "title": "Early Work",
            "node_id": "0010",
            "start_index": 1,
            "end_index": 1,
            "summary": "Early seq2seq work.",
            "text": "The first seq2seq paper...",
        }
    ]
    result = render_source_md(sample_tree, "test.pdf", "doc-001")
    assert "### Early Work" in result
```

- [ ] **Step 2: Run tests — should fail**

Run: `pytest tests/test_tree_renderer.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement tree_renderer.py**

```python
# openkb/tree_renderer.py


def _render_nodes(nodes: list[dict], depth: int, include_text: bool) -> list[str]:
    """Recursively render tree nodes as markdown."""
    lines: list[str] = []
    prefix = "#" * min(depth, 6)  # Markdown supports max 6 heading levels

    for node in nodes:
        title = node.get("title", "Untitled")
        start = node.get("start_index", "?")
        end = node.get("end_index", "?")

        lines.append(f"{prefix} {title}")
        if start == end:
            lines.append(f"(page {start})")
        else:
            lines.append(f"(pages {start}–{end})")

        if include_text:
            text = node.get("text", "")
            if text:
                lines.append("")
                lines.append(text)
        else:
            summary = node.get("summary", "")
            if summary:
                lines.append(f"Summary: {summary}")

        lines.append("")

        children = node.get("nodes", [])
        if children:
            lines.extend(_render_nodes(children, depth + 1, include_text))

    return lines


def _frontmatter(source: str, doc_id: str) -> str:
    return f"---\nsource: {source}\ntype: pageindex\ndoc_id: {doc_id}\n---\n"


def render_source_md(tree: dict, source_name: str, doc_id: str) -> str:
    """Render PageIndex tree as a readable markdown file with full node text."""
    lines = [_frontmatter(source_name, doc_id)]
    lines.extend(_render_nodes(tree.get("structure", []), depth=1, include_text=True))
    return "\n".join(lines)


def render_summary_md(tree: dict, source_name: str, doc_id: str) -> str:
    """Render PageIndex tree as a summary markdown (summaries only, no text)."""
    lines = [_frontmatter(source_name, doc_id)]
    lines.extend(_render_nodes(tree.get("structure", []), depth=1, include_text=False))
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests — should pass**

Run: `pytest tests/test_tree_renderer.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add openkb/tree_renderer.py tests/test_tree_renderer.py
git commit -m "feat: PageIndex tree to markdown renderer"
```

---

## Task 7: PageIndex Indexer Integration

**Files:**
- Create: `openkb/indexer.py`
- Create: `tests/test_indexer.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_indexer.py
from pathlib import Path
from unittest.mock import patch, MagicMock

from openkb.indexer import index_long_document


@patch("openkb.indexer.LocalClient")
def test_index_long_document(mock_client_cls, kb_dir, sample_tree):
    # Mock PageIndex SDK
    mock_col = MagicMock()
    mock_col.add.return_value = "doc-001"
    mock_col.get_document.return_value = {
        "doc_description": sample_tree["doc_description"]
    }
    mock_col.get_document_structure.return_value = sample_tree["structure"]

    mock_client = MagicMock()
    mock_client.collection.return_value = mock_col
    mock_client_cls.return_value = mock_client

    pdf_path = kb_dir / "raw" / "test.pdf"
    pdf_path.write_bytes(b"%PDF-fake")

    result = index_long_document(pdf_path, kb_dir)

    assert result.doc_id == "doc-001"
    assert result.description == sample_tree["doc_description"]

    source_md = (kb_dir / "wiki" / "sources" / "test.md").read_text()
    assert "type: pageindex" in source_md
    assert "# Introduction" in source_md

    summary_md = (kb_dir / "wiki" / "summaries" / "test.md").read_text()
    assert "Summary:" in summary_md
    assert "type: pageindex" in summary_md
```

- [ ] **Step 2: Run tests — should fail**

Run: `pytest tests/test_indexer.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement indexer.py**

```python
# openkb/indexer.py
import logging
from dataclasses import dataclass
from pathlib import Path

from openkb.config import load_config
from openkb.tree_renderer import render_source_md, render_summary_md

logger = logging.getLogger(__name__)


@dataclass
class IndexResult:
    doc_id: str
    description: str
    tree: dict


def index_long_document(pdf_path: Path, kb_dir: Path) -> IndexResult:
    """Index a long PDF using PageIndex SDK."""
    from pageindex import LocalClient
    from pageindex.config import IndexConfig

    config = load_config(kb_dir / ".okb" / "config.yaml")

    index_config = IndexConfig(
        model=config["model"],
        if_add_node_text=True,
        if_add_node_summary=True,
        if_add_doc_description=True,
    )

    client = LocalClient(
        model=config["model"],
        storage_path=str(kb_dir / ".okb" / "pageindex.db"),
        index_config=index_config,
    )
    col = client.collection()

    doc_id = col.add(str(pdf_path))
    doc_meta = col.get_document(doc_id)
    structure = col.get_document_structure(doc_id)

    description = doc_meta.get("doc_description", pdf_path.stem)

    tree = {
        "doc_name": pdf_path.name,
        "doc_description": description,
        "structure": structure,
    }

    doc_stem = pdf_path.stem

    # Write sources/ (full text)
    source_md = render_source_md(tree, pdf_path.name, doc_id)
    (kb_dir / "wiki" / "sources" / f"{doc_stem}.md").write_text(
        source_md, encoding="utf-8"
    )

    # Write summaries/ (tree summaries only)
    summary_md = render_summary_md(tree, pdf_path.name, doc_id)
    (kb_dir / "wiki" / "summaries" / f"{doc_stem}.md").write_text(
        summary_md, encoding="utf-8"
    )

    return IndexResult(doc_id=doc_id, description=description, tree=tree)
```

- [ ] **Step 4: Run tests — should pass**

Run: `pytest tests/test_indexer.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add openkb/indexer.py tests/test_indexer.py
git commit -m "feat: PageIndex integration for long document indexing"
```

---

## Task 8: Agent Tools

**Files:**
- Create: `openkb/agent/__init__.py`
- Create: `openkb/agent/tools.py`
- Create: `tests/test_agent_tools.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_agent_tools.py
from pathlib import Path

from openkb.agent.tools import list_wiki_files, read_wiki_file, write_wiki_file


def test_list_wiki_files(kb_dir):
    (kb_dir / "wiki" / "concepts" / "attention.md").write_text("content")
    (kb_dir / "wiki" / "concepts" / "transformer.md").write_text("content")

    result = list_wiki_files("concepts", str(kb_dir / "wiki"))
    assert "attention.md" in result
    assert "transformer.md" in result


def test_list_wiki_files_empty(kb_dir):
    result = list_wiki_files("concepts", str(kb_dir / "wiki"))
    assert result == "No files found."


def test_read_wiki_file(kb_dir):
    (kb_dir / "wiki" / "concepts" / "attention.md").write_text("# Attention\nDetails.")
    result = read_wiki_file("concepts/attention.md", str(kb_dir / "wiki"))
    assert "# Attention" in result
    assert "Details." in result


def test_read_wiki_file_not_found(kb_dir):
    result = read_wiki_file("concepts/missing.md", str(kb_dir / "wiki"))
    assert "not found" in result.lower()


def test_write_wiki_file(kb_dir):
    write_wiki_file("concepts/new.md", "# New Concept\nContent.", str(kb_dir / "wiki"))
    assert (kb_dir / "wiki" / "concepts" / "new.md").read_text() == "# New Concept\nContent."


def test_write_wiki_file_update(kb_dir):
    path = kb_dir / "wiki" / "concepts" / "existing.md"
    path.write_text("old content")
    write_wiki_file("concepts/existing.md", "new content", str(kb_dir / "wiki"))
    assert path.read_text() == "new content"
```

- [ ] **Step 2: Run tests — should fail**

Run: `pytest tests/test_agent_tools.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement agent tools**

```python
# openkb/agent/__init__.py
```

```python
# openkb/agent/tools.py
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def list_wiki_files(directory: str, wiki_root: str) -> str:
    """List filenames in a wiki/ subdirectory."""
    dir_path = Path(wiki_root) / directory
    if not dir_path.is_dir():
        return f"Directory '{directory}' not found."
    files = sorted(f.name for f in dir_path.iterdir() if f.is_file() and f.suffix == ".md")
    if not files:
        return "No files found."
    return "\n".join(files)


def read_wiki_file(path: str, wiki_root: str) -> str:
    """Read a .md file from wiki/."""
    file_path = Path(wiki_root) / path
    if not file_path.exists():
        return f"File not found: {path}"
    return file_path.read_text(encoding="utf-8")


def write_wiki_file(path: str, content: str, wiki_root: str) -> str:
    """Write/update a .md file in wiki/."""
    file_path = Path(wiki_root) / path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    return f"Written: {path}"
```

- [ ] **Step 4: Run tests — should pass**

Run: `pytest tests/test_agent_tools.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add openkb/agent/ tests/test_agent_tools.py
git commit -m "feat: agent wiki file tools (list, read, write)"
```

---

## Task 9: Wiki Compilation Agent

**Files:**
- Create: `openkb/agent/compiler.py`
- Create: `tests/test_compiler.py`

- [ ] **Step 1: Write test for compiler setup**

```python
# tests/test_compiler.py
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from openkb.agent.compiler import build_compiler_agent, compile_short_doc, compile_long_doc


def test_build_compiler_agent():
    agent = build_compiler_agent("/tmp/wiki", model="gpt-4o")
    assert agent.name == "wiki-compiler"
    assert len(agent.tools) >= 3  # list, read, write at minimum


def test_build_compiler_agent_has_schema_in_instructions():
    agent = build_compiler_agent("/tmp/wiki", model="gpt-4o")
    assert "Wiki Schema" in agent.instructions


@pytest.mark.asyncio
@patch("openkb.agent.compiler.Runner")
async def test_compile_short_doc(mock_runner_cls, kb_dir):
    # Set up wiki state
    (kb_dir / "wiki" / "index.md").write_text("# Knowledge Base Index\n\n## Documents\n\n## Concepts\n")
    source = kb_dir / "wiki" / "sources" / "paper.md"
    source.write_text("# Paper\nContent about attention.")

    mock_result = MagicMock()
    mock_result.final_output = "Compilation complete."
    mock_runner_cls.run = AsyncMock(return_value=mock_result)

    await compile_short_doc(
        doc_name="paper.pdf",
        source_path=source,
        kb_dir=kb_dir,
        model="gpt-4o",
    )

    mock_runner_cls.run.assert_called_once()
    call_args = mock_runner_cls.run.call_args
    # Verify the user message contains the source content
    assert "attention" in call_args.kwargs.get("input", call_args.args[1] if len(call_args.args) > 1 else "")


@pytest.mark.asyncio
@patch("openkb.agent.compiler.Runner")
async def test_compile_long_doc(mock_runner_cls, kb_dir):
    (kb_dir / "wiki" / "index.md").write_text("# Knowledge Base Index\n\n## Documents\n\n## Concepts\n")
    summary = kb_dir / "wiki" / "summaries" / "textbook.md"
    summary.write_text("---\nsource: textbook.pdf\ntype: pageindex\ndoc_id: abc\n---\n# Ch1\nSummary: Intro.")

    mock_result = MagicMock()
    mock_result.final_output = "Done."
    mock_runner_cls.run = AsyncMock(return_value=mock_result)

    await compile_long_doc(
        doc_name="textbook.pdf",
        summary_path=summary,
        doc_id="abc",
        kb_dir=kb_dir,
        model="gpt-4o",
    )

    mock_runner_cls.run.assert_called_once()
```

- [ ] **Step 2: Run tests — should fail**

Run: `pytest tests/test_compiler.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement compiler.py**

```python
# openkb/agent/compiler.py
import logging
from functools import partial
from pathlib import Path

from agents import Agent, Runner, function_tool

from openkb.agent.tools import list_wiki_files, read_wiki_file, write_wiki_file
from openkb.schema import SCHEMA_MD

logger = logging.getLogger(__name__)


def build_compiler_agent(wiki_root: str, model: str) -> Agent:
    """Build the wiki compilation agent with tools bound to wiki_root."""

    @function_tool
    def tool_list_wiki_files(directory: str) -> str:
        """List .md filenames in a wiki subdirectory (e.g., 'concepts', 'summaries')."""
        return list_wiki_files(directory, wiki_root)

    @function_tool
    def tool_read_wiki_file(path: str) -> str:
        """Read a .md file from the wiki. Path is relative to wiki root (e.g., 'concepts/attention.md')."""
        return read_wiki_file(path, wiki_root)

    @function_tool
    def tool_write_wiki_file(path: str, content: str) -> str:
        """Write or update a .md file in the wiki. Path is relative to wiki root."""
        return write_wiki_file(path, content, wiki_root)

    instructions = f"""You are a knowledge base wiki compiler. Your job is to organize
document knowledge into a structured wiki.

{SCHEMA_MD}

When given a new document:
1. Generate a summary and write it to summaries/{{doc_name}}.md (for short docs only —
   long docs already have summaries generated).
2. Read index.md to understand the existing knowledge structure.
3. List and read relevant concept pages in concepts/.
4. Create new concept pages or update existing ones to incorporate the new document's knowledge.
   Use [[wikilinks]] to cross-link pages.
5. Update index.md with the new document entry and any new concepts.

Keep pages focused. Use [[wikilinks]] for cross-references. Be thorough but concise."""

    return Agent(
        name="wiki-compiler",
        instructions=instructions,
        tools=[tool_list_wiki_files, tool_read_wiki_file, tool_write_wiki_file],
        model=model,
    )


async def compile_short_doc(
    doc_name: str, source_path: Path, kb_dir: Path, model: str
) -> None:
    """Run compilation agent for a short document (full text available)."""
    wiki_root = str(kb_dir / "wiki")
    agent = build_compiler_agent(wiki_root, model)
    source_content = source_path.read_text(encoding="utf-8")

    input_message = (
        f"New document added: {doc_name}\n\n"
        f"Full text:\n\n{source_content}\n\n"
        "Please generate a summary, update concepts, and update the index."
    )

    result = await Runner.run(agent, input=input_message)
    logger.info("Compilation result: %s", result.final_output)


async def compile_long_doc(
    doc_name: str, summary_path: Path, doc_id: str, kb_dir: Path, model: str
) -> None:
    """Run compilation agent for a long document (only summary tree available)."""
    wiki_root = str(kb_dir / "wiki")
    agent = build_compiler_agent(wiki_root, model)
    summary_content = summary_path.read_text(encoding="utf-8")

    input_message = (
        f"New long document added: {doc_name} (doc_id: {doc_id})\n\n"
        f"This document's summary is already generated at summaries/{Path(doc_name).stem}.md.\n"
        f"Here is the summary tree:\n\n{summary_content}\n\n"
        "Please update concepts and the index based on these summaries. "
        "Do NOT regenerate the summary — it is already complete."
    )

    result = await Runner.run(agent, input=input_message)
    logger.info("Compilation result: %s", result.final_output)
```

- [ ] **Step 4: Run tests — should pass**

Run: `pytest tests/test_compiler.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add openkb/agent/compiler.py tests/test_compiler.py
git commit -m "feat: wiki compilation agent with single-session design"
```

---

## Task 10: okb add Orchestrator

**Files:**
- Modify: `openkb/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write tests for okb add**

```python
# Append to tests/test_cli.py
from unittest.mock import patch, MagicMock, AsyncMock


@patch("openkb.cli._add_single_file")
def test_add_single_file(mock_add, tmp_path):
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path) as td:
        runner.invoke(cli, ["init"])
        f = Path(td) / "paper.md"
        f.write_text("# Paper\nContent")
        result = runner.invoke(cli, ["add", str(f)])
        assert result.exit_code == 0
        mock_add.assert_called_once()


@patch("openkb.cli._add_single_file")
def test_add_directory(mock_add, tmp_path):
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path) as td:
        runner.invoke(cli, ["init"])
        d = Path(td) / "docs"
        d.mkdir()
        (d / "a.md").write_text("a")
        (d / "b.pdf").write_bytes(b"%PDF")
        (d / "c.txt").write_text("c")  # unsupported, should be skipped or tried
        result = runner.invoke(cli, ["add", str(d)])
        assert result.exit_code == 0
        assert mock_add.call_count >= 2


def test_add_no_init(tmp_path):
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        f = Path("test.md")
        f.write_text("content")
        result = runner.invoke(cli, ["add", str(f)])
        assert result.exit_code != 0 or "not initialized" in result.output.lower()
```

- [ ] **Step 2: Run tests — should fail**

Run: `pytest tests/test_cli.py::test_add_single_file -v`
Expected: FAIL

- [ ] **Step 3: Implement okb add in cli.py**

```python
# Add these imports at the top of openkb/cli.py
import asyncio
import logging
from pathlib import Path

from openkb.converter import convert_document
from openkb.indexer import index_long_document
from openkb.agent.compiler import compile_short_doc, compile_long_doc
from openkb.config import load_config

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {
    ".pdf", ".md", ".markdown", ".docx", ".pptx", ".xlsx",
    ".html", ".htm", ".txt", ".csv", ".json", ".xml",
    ".jpg", ".jpeg", ".png", ".gif", ".bmp",
    ".mp3", ".wav",
}


def _find_kb_dir() -> Path | None:
    """Find the knowledge base root (directory containing .okb/)."""
    cwd = Path.cwd()
    if (cwd / ".okb").is_dir():
        return cwd
    return None


def _add_single_file(file_path: Path, kb_dir: Path) -> None:
    """Process a single file through the full add pipeline."""
    config = load_config(kb_dir / ".okb" / "config.yaml")
    model = config["model"]

    click.echo(f"Processing {file_path.name}...")

    # Step 1: Convert
    result = convert_document(file_path, kb_dir)
    if result.skipped:
        click.echo(f"  Skipped (duplicate): {file_path.name}")
        return

    # Step 2: Handle long doc vs short doc
    if result.is_long_doc:
        click.echo(f"  Long document detected — indexing with PageIndex...")
        try:
            idx = index_long_document(result.raw_path, kb_dir)
        except Exception as e:
            click.echo(f"  Error indexing {file_path.name}: {e}")
            return
        click.echo(f"  Compiling to wiki...")
        summary_path = kb_dir / "wiki" / "summaries" / f"{file_path.stem}.md"
        try:
            asyncio.run(
                compile_long_doc(file_path.name, summary_path, idx.doc_id, kb_dir, model)
            )
        except Exception as e:
            click.echo(f"  Error compiling {file_path.name}: {e}")
            return
    else:
        click.echo(f"  Compiling to wiki...")
        try:
            asyncio.run(
                compile_short_doc(file_path.name, result.source_path, kb_dir, model)
            )
        except Exception as e:
            click.echo(f"  Error compiling {file_path.name}: {e}")
            return

    click.echo(f"  Done: {file_path.name}")


# Replace the existing add command
@cli.command()
@click.argument("path", type=click.Path(exists=True))
def add(path):
    """Add a document or directory to the knowledge base."""
    kb_dir = _find_kb_dir()
    if kb_dir is None:
        click.echo("Error: Knowledge base not initialized. Run 'okb init' first.")
        raise SystemExit(1)

    target = Path(path)
    if target.is_dir():
        files = sorted(
            f for f in target.rglob("*")
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
        )
        if not files:
            click.echo("No supported files found in directory.")
            return
        click.echo(f"Found {len(files)} files to process.")
        for f in files:
            try:
                _add_single_file(f, kb_dir)
            except Exception as e:
                click.echo(f"  Error processing {f.name}: {e}")
                continue
    else:
        _add_single_file(target, kb_dir)
```

- [ ] **Step 4: Run tests — should pass**

Run: `pytest tests/test_cli.py -v`
Expected: All tests pass (including init tests from Task 3).

- [ ] **Step 5: Commit**

```bash
git add openkb/cli.py tests/test_cli.py
git commit -m "feat: implement okb add with short/long doc paths"
```

---

## Task 11: Q&A Agent

**Files:**
- Create: `openkb/agent/query.py`
- Create: `tests/test_query.py`
- Modify: `openkb/cli.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_query.py
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from openkb.agent.query import build_query_agent, pageindex_retrieve


def test_build_query_agent():
    agent = build_query_agent("/tmp/wiki", "/tmp/.okb/pageindex.db", model="gpt-4o")
    assert agent.name == "qa-agent"
    assert len(agent.tools) >= 3  # list, read, pageindex_retrieve


def test_build_query_agent_has_schema():
    agent = build_query_agent("/tmp/wiki", "/tmp/db", model="gpt-4o")
    assert "Wiki Schema" in agent.instructions


@patch("openkb.agent.query._llm_select_pages")
@patch("openkb.agent.query._get_structure")
@patch("openkb.agent.query._get_pages")
def test_pageindex_retrieve(mock_get_pages, mock_get_struct, mock_select):
    mock_get_struct.return_value = [
        {"title": "Ch1", "node_id": "0000", "start_index": 1, "end_index": 10},
        {"title": "Ch2", "node_id": "0001", "start_index": 11, "end_index": 20},
    ]
    mock_select.return_value = [1, 2, 3]
    mock_get_pages.return_value = [
        {"page": 1, "content": "Page 1 text"},
        {"page": 2, "content": "Page 2 text"},
        {"page": 3, "content": "Page 3 text"},
    ]

    result = pageindex_retrieve("doc-001", "What is attention?", "/tmp/db", "gpt-4o")

    assert "Page 1 text" in result
    assert "Page 3 text" in result
```

- [ ] **Step 2: Run tests — should fail**

Run: `pytest tests/test_query.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement query.py**

```python
# openkb/agent/query.py
import json
import logging
from pathlib import Path

from agents import Agent, Runner, function_tool

from openkb.agent.tools import list_wiki_files, read_wiki_file
from openkb.schema import SCHEMA_MD

logger = logging.getLogger(__name__)


def _get_structure(doc_id: str, db_path: str) -> list[dict]:
    """Get document structure from PageIndex."""
    from pageindex import LocalClient

    client = LocalClient(storage_path=db_path)
    col = client.collection()
    return col.get_document_structure(doc_id)


def _get_pages(doc_id: str, pages: list[int], db_path: str) -> list[dict]:
    """Get specific pages from PageIndex."""
    from pageindex import LocalClient

    client = LocalClient(storage_path=db_path)
    col = client.collection()
    return col.get_page_content(doc_id, pages)


def _llm_select_pages(structure: list[dict], question: str, model: str) -> list[int]:
    """Ask LLM which pages are relevant to the question."""
    import litellm

    structure_str = json.dumps(structure, indent=2, ensure_ascii=False)
    prompt = (
        f"Given this document structure:\n{structure_str}\n\n"
        f"Question: {question}\n\n"
        "Return a JSON array of page numbers (integers) that are most relevant. "
        "Only return the JSON array, nothing else."
    )
    response = litellm.completion(
        model=model,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.choices[0].message.content.strip()
    # Parse JSON array from response
    if text.startswith("["):
        return json.loads(text)
    # Try to extract array from markdown code block
    if "```" in text:
        text = text.split("```")[1].strip()
        if text.startswith("json"):
            text = text[4:].strip()
        return json.loads(text)
    return []


def pageindex_retrieve(
    doc_id: str, question: str, db_path: str, model: str
) -> str:
    """Retrieve relevant content from a long document via PageIndex tree search."""
    structure = _get_structure(doc_id, db_path)
    pages = _llm_select_pages(structure, question, model)
    if not pages:
        return "No relevant pages found."
    page_data = _get_pages(doc_id, pages, db_path)
    parts = [f"[Page {p['page']}]\n{p['content']}" for p in page_data]
    return "\n\n---\n\n".join(parts)


def build_query_agent(wiki_root: str, db_path: str, model: str) -> Agent:
    """Build the Q&A agent with wiki and PageIndex tools."""

    @function_tool
    def tool_list_wiki_files(directory: str) -> str:
        """List .md filenames in a wiki subdirectory."""
        return list_wiki_files(directory, wiki_root)

    @function_tool
    def tool_read_wiki_file(path: str) -> str:
        """Read a .md file from the wiki."""
        return read_wiki_file(path, wiki_root)

    @function_tool
    def tool_pageindex_retrieve(doc_id: str, question: str) -> str:
        """Retrieve relevant content from a long document using PageIndex tree search.
        Use this for documents marked 'pageindex' in index.md."""
        return pageindex_retrieve(doc_id, question, db_path, model)

    instructions = f"""You are a knowledge base Q&A agent. Answer questions using the wiki.

{SCHEMA_MD}

Steps:
1. Read index.md to see all documents and concepts.
2. Read relevant concept pages and summary pages.
3. For short documents (marked 'short' in index.md), read sources/{{name}}.md for full text.
4. For long documents (marked 'pageindex' in index.md), use pageindex_retrieve(doc_id, question).
5. Synthesize a clear, accurate answer with references to source documents."""

    return Agent(
        name="qa-agent",
        instructions=instructions,
        tools=[tool_list_wiki_files, tool_read_wiki_file, tool_pageindex_retrieve],
        model=model,
    )


async def run_query(question: str, kb_dir: Path, model: str) -> str:
    """Run a Q&A query against the knowledge base."""
    wiki_root = str(kb_dir / "wiki")
    db_path = str(kb_dir / ".okb" / "pageindex.db")
    agent = build_query_agent(wiki_root, db_path, model)
    result = await Runner.run(agent, input=question)
    return result.final_output
```

- [ ] **Step 4: Run tests — should pass**

Run: `pytest tests/test_query.py -v`
Expected: 3 passed.

- [ ] **Step 5: Wire up okb query in cli.py**

Replace the `query` command in `openkb/cli.py`:

```python
@cli.command()
@click.argument("question")
def query(question):
    """Ask a question against the knowledge base."""
    kb_dir = _find_kb_dir()
    if kb_dir is None:
        click.echo("Error: Knowledge base not initialized. Run 'okb init' first.")
        raise SystemExit(1)

    config = load_config(kb_dir / ".okb" / "config.yaml")
    try:
        answer = asyncio.run(
            __import__("openkb.agent.query", fromlist=["run_query"]).run_query(
                question, kb_dir, config["model"]
            )
        )
        click.echo(answer)
    except Exception as e:
        click.echo(f"Error: {e}")
        raise SystemExit(1)
```

- [ ] **Step 6: Commit**

```bash
git add openkb/agent/query.py tests/test_query.py openkb/cli.py
git commit -m "feat: Q&A agent with pageindex_retrieve for long docs"
```

---

## Task 12: Watch Mode

**Files:**
- Create: `openkb/watcher.py`
- Create: `tests/test_watcher.py`
- Modify: `openkb/cli.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_watcher.py
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

from openkb.watcher import DebouncedHandler


def test_debounced_handler_collects_files(tmp_path):
    handler = DebouncedHandler(callback=MagicMock(), debounce_seconds=0.1)
    event = MagicMock()
    event.src_path = str(tmp_path / "test.pdf")
    event.is_directory = False

    handler.on_created(event)

    assert str(tmp_path / "test.pdf") in handler.pending


def test_debounced_handler_ignores_directories(tmp_path):
    handler = DebouncedHandler(callback=MagicMock(), debounce_seconds=0.1)
    event = MagicMock()
    event.src_path = str(tmp_path / "subdir")
    event.is_directory = True

    handler.on_created(event)

    assert len(handler.pending) == 0


def test_debounced_handler_ignores_hidden_files(tmp_path):
    handler = DebouncedHandler(callback=MagicMock(), debounce_seconds=0.1)
    event = MagicMock()
    event.src_path = str(tmp_path / ".DS_Store")
    event.is_directory = False

    handler.on_created(event)

    assert len(handler.pending) == 0
```

- [ ] **Step 2: Run tests — should fail**

Run: `pytest tests/test_watcher.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement watcher.py**

```python
# openkb/watcher.py
import logging
import signal
import threading
import time
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger(__name__)


class DebouncedHandler(FileSystemEventHandler):
    """File system handler that debounces events and batches processing."""

    def __init__(self, callback: Callable[[list[Path]], None], debounce_seconds: float = 2.0):
        super().__init__()
        self.callback = callback
        self.debounce_seconds = debounce_seconds
        self.pending: set[str] = set()
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def _on_file_event(self, event) -> None:
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.name.startswith("."):
            return

        with self._lock:
            self.pending.add(event.src_path)
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.debounce_seconds, self._flush)
            self._timer.start()

    def on_created(self, event):
        self._on_file_event(event)

    def on_modified(self, event):
        self._on_file_event(event)

    def _flush(self) -> None:
        with self._lock:
            paths = [Path(p) for p in sorted(self.pending)]
            self.pending.clear()
        if paths:
            self.callback(paths)


def watch_directory(
    raw_dir: Path, callback: Callable[[list[Path]], None], debounce: float = 2.0
) -> None:
    """Watch a directory for new files with debounce. Blocks until Ctrl+C."""
    handler = DebouncedHandler(callback, debounce_seconds=debounce)
    observer = Observer()
    observer.schedule(handler, str(raw_dir), recursive=True)
    observer.start()

    stop = threading.Event()

    def _signal_handler(sig, frame):
        logger.info("Shutting down watcher...")
        stop.set()

    signal.signal(signal.SIGINT, _signal_handler)

    try:
        while not stop.is_set():
            stop.wait(timeout=1)
    finally:
        observer.stop()
        observer.join()
```

- [ ] **Step 4: Run tests — should pass**

Run: `pytest tests/test_watcher.py -v`
Expected: 3 passed.

- [ ] **Step 5: Wire up okb watch in cli.py**

Replace the `watch` command in `openkb/cli.py`:

```python
@cli.command()
def watch():
    """Watch raw/ directory and auto-compile new files."""
    kb_dir = _find_kb_dir()
    if kb_dir is None:
        click.echo("Error: Knowledge base not initialized. Run 'okb init' first.")
        raise SystemExit(1)

    raw_dir = kb_dir / "raw"
    click.echo(f"Watching {raw_dir} for new files... (Ctrl+C to stop)")

    def on_new_files(files):
        for f in files:
            if f.suffix.lower() in SUPPORTED_EXTENSIONS:
                try:
                    _add_single_file(f, kb_dir)
                except Exception as e:
                    click.echo(f"Error processing {f.name}: {e}")

    from openkb.watcher import watch_directory
    watch_directory(raw_dir, on_new_files)
```

- [ ] **Step 6: Commit**

```bash
git add openkb/watcher.py tests/test_watcher.py openkb/cli.py
git commit -m "feat: watch mode with debounced file system monitoring"
```

---

## Task 13: Structural Lint

**Files:**
- Create: `openkb/lint.py`
- Create: `tests/test_lint.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_lint.py
from pathlib import Path

from openkb.lint import (
    find_broken_links,
    find_orphans,
    find_missing_entries,
    check_index_sync,
    run_structural_lint,
)


def test_find_broken_links(kb_dir):
    wiki = kb_dir / "wiki"
    (wiki / "concepts" / "attention.md").write_text(
        "See [[concepts/softmax]] and [[summaries/paper]]."
    )
    (wiki / "summaries" / "paper.md").write_text("Paper summary.")

    issues = find_broken_links(wiki)

    assert len(issues) == 1
    assert "softmax" in issues[0]


def test_no_broken_links(kb_dir):
    wiki = kb_dir / "wiki"
    (wiki / "concepts" / "attention.md").write_text("See [[summaries/paper]].")
    (wiki / "summaries" / "paper.md").write_text("Summary.")

    issues = find_broken_links(wiki)
    assert len(issues) == 0


def test_find_orphans(kb_dir):
    wiki = kb_dir / "wiki"
    (wiki / "summaries" / "paper.md").write_text("No links here.")
    (wiki / "concepts" / "attention.md").write_text("See [[summaries/paper]].")
    (wiki / "summaries" / "orphan.md").write_text("No links here either.")

    issues = find_orphans(wiki)

    orphan_files = [i for i in issues if "orphan.md" in i]
    assert len(orphan_files) == 1


def test_find_missing_entries(kb_dir):
    (kb_dir / "raw" / "paper.pdf").write_bytes(b"%PDF")
    (kb_dir / "raw" / "notes.md").write_text("notes")
    # Only paper has sources/summaries
    (kb_dir / "wiki" / "sources" / "paper.md").write_text("source")
    (kb_dir / "wiki" / "summaries" / "paper.md").write_text("summary")

    issues = find_missing_entries(kb_dir / "raw", kb_dir / "wiki")

    assert any("notes" in i for i in issues)


def test_check_index_sync(kb_dir):
    wiki = kb_dir / "wiki"
    (wiki / "index.md").write_text(
        "## Documents\n- [[summaries/paper]]\n\n## Concepts\n- [[concepts/attention]]\n"
    )
    (wiki / "summaries" / "paper.md").write_text("exists")
    # attention.md does NOT exist

    issues = check_index_sync(wiki)

    assert any("attention" in i for i in issues)


def test_run_structural_lint(kb_dir):
    (kb_dir / "raw" / "paper.pdf").write_bytes(b"%PDF")
    wiki = kb_dir / "wiki"
    (wiki / "index.md").write_text("## Documents\n\n## Concepts\n")
    (wiki / "summaries" / "paper.md").write_text("Summary with [[concepts/missing]].")
    (wiki / "sources" / "paper.md").write_text("source")

    report = run_structural_lint(kb_dir)

    assert "Broken link" in report or "Missing" in report or len(report) > 0
```

- [ ] **Step 2: Run tests — should fail**

Run: `pytest tests/test_lint.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement lint.py**

```python
# openkb/lint.py
import re
from pathlib import Path

_WIKILINK_PATTERN = re.compile(r"\[\[([^\]]+)\]\]")


def _find_all_md_files(wiki: Path) -> list[Path]:
    """Find all .md files in wiki/ excluding SCHEMA.md."""
    return [
        f
        for f in wiki.rglob("*.md")
        if f.name != "SCHEMA.md" and "images" not in f.parts
    ]


def _extract_wikilinks(content: str) -> list[str]:
    """Extract all [[wikilink]] targets from content."""
    return _WIKILINK_PATTERN.findall(content)


def find_broken_links(wiki: Path) -> list[str]:
    """Find [[wikilinks]] that point to non-existent files."""
    issues = []
    for md_file in _find_all_md_files(wiki):
        content = md_file.read_text(encoding="utf-8")
        for link in _extract_wikilinks(content):
            target = wiki / f"{link}.md"
            if not target.exists():
                rel = md_file.relative_to(wiki)
                issues.append(f"Broken link: {rel} -> [[{link}]] (not found)")
    return issues


def find_orphans(wiki: Path) -> list[str]:
    """Find pages with no incoming or outgoing links."""
    all_files = _find_all_md_files(wiki)
    # Collect all link targets and sources
    linked_to: set[str] = set()
    has_outgoing: set[str] = set()

    for md_file in all_files:
        content = md_file.read_text(encoding="utf-8")
        links = _extract_wikilinks(content)
        rel = str(md_file.relative_to(wiki)).removesuffix(".md")
        if links:
            has_outgoing.add(rel)
        for link in links:
            linked_to.add(link)

    issues = []
    for md_file in all_files:
        if md_file.name == "index.md":
            continue
        rel = str(md_file.relative_to(wiki)).removesuffix(".md")
        if rel not in linked_to and rel not in has_outgoing:
            issues.append(f"Orphan: {rel}.md (no links to/from any page)")
    return issues


def find_missing_entries(raw: Path, wiki: Path) -> list[str]:
    """Find files in raw/ that have no corresponding sources/ or summaries/."""
    issues = []
    for f in raw.iterdir():
        if f.is_file() and not f.name.startswith("."):
            stem = f.stem
            has_source = (wiki / "sources" / f"{stem}.md").exists()
            has_summary = (wiki / "summaries" / f"{stem}.md").exists()
            if not has_source or not has_summary:
                missing = []
                if not has_source:
                    missing.append("sources/")
                if not has_summary:
                    missing.append("summaries/")
                issues.append(
                    f"Missing entries: {f.name} has no {' or '.join(missing)} entry"
                )
    return issues


def check_index_sync(wiki: Path) -> list[str]:
    """Check that index.md entries match actual files."""
    index_path = wiki / "index.md"
    if not index_path.exists():
        return ["index.md not found"]

    content = index_path.read_text(encoding="utf-8")
    links = _extract_wikilinks(content)

    issues = []
    for link in links:
        target = wiki / f"{link}.md"
        if not target.exists():
            issues.append(f"Index out of sync: [[{link}]] in index.md but file not found")
    return issues


def run_structural_lint(kb_dir: Path) -> str:
    """Run all structural checks and return a formatted report."""
    wiki = kb_dir / "wiki"
    raw = kb_dir / "raw"

    all_issues = []
    all_issues.extend(find_broken_links(wiki))
    all_issues.extend(find_orphans(wiki))
    if raw.exists():
        all_issues.extend(find_missing_entries(raw, wiki))
    all_issues.extend(check_index_sync(wiki))

    if not all_issues:
        return "No structural issues found."

    lines = ["## Structural Issues", ""]
    for issue in all_issues:
        lines.append(f"- {issue}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests — should pass**

Run: `pytest tests/test_lint.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add openkb/lint.py tests/test_lint.py
git commit -m "feat: structural lint checks (broken links, orphans, missing, sync)"
```

---

## Task 14: Knowledge Lint Agent + okb lint Command

**Files:**
- Create: `openkb/agent/linter.py`
- Modify: `openkb/cli.py`

- [ ] **Step 1: Write tests for linter agent setup**

```python
# Append to tests/test_lint.py
from openkb.agent.linter import build_lint_agent


def test_build_lint_agent():
    agent = build_lint_agent("/tmp/wiki", model="gpt-4o")
    assert agent.name == "lint-agent"
    assert len(agent.tools) >= 2  # list, read


def test_build_lint_agent_instructions():
    agent = build_lint_agent("/tmp/wiki", model="gpt-4o")
    assert "contradiction" in agent.instructions.lower() or "Contradiction" in agent.instructions
```

- [ ] **Step 2: Implement linter.py**

```python
# openkb/agent/linter.py
import logging
from pathlib import Path

from agents import Agent, Runner, function_tool

from openkb.agent.tools import list_wiki_files, read_wiki_file

logger = logging.getLogger(__name__)


def build_lint_agent(wiki_root: str, model: str) -> Agent:
    """Build the knowledge lint agent."""

    @function_tool
    def tool_list_wiki_files(directory: str) -> str:
        """List .md filenames in a wiki subdirectory."""
        return list_wiki_files(directory, wiki_root)

    @function_tool
    def tool_read_wiki_file(path: str) -> str:
        """Read a .md file from the wiki."""
        return read_wiki_file(path, wiki_root)

    instructions = """You are a knowledge base quality checker. Analyze the wiki for:

1. Contradictions — conflicting information across related pages
2. Gaps — concepts referenced in multiple summaries but lacking a dedicated concept page
3. Staleness — documents that aren't reflected in concept pages
4. Quality — concept pages that don't adequately synthesize their source documents

Process:
1. Read index.md to get the full list of documents and concepts.
2. For each concept page, read it and its referenced summaries.
3. Check for the issues listed above.
4. Return a structured report in markdown format with specific findings.

Format each finding as:
- Type (Contradiction/Gap/Stale/Quality)
- Which files are involved
- Description of the issue

Be thorough but only report real issues, not speculation."""

    return Agent(
        name="lint-agent",
        instructions=instructions,
        tools=[tool_list_wiki_files, tool_read_wiki_file],
        model=model,
    )


async def run_knowledge_lint(kb_dir: Path, model: str) -> str:
    """Run LLM-based knowledge checks."""
    wiki_root = str(kb_dir / "wiki")
    agent = build_lint_agent(wiki_root, model)
    result = await Runner.run(
        agent, input="Please analyze the entire wiki for knowledge issues."
    )
    return result.final_output
```

- [ ] **Step 3: Wire up okb lint in cli.py**

Replace the `lint` command in `openkb/cli.py`:

```python
@cli.command()
@click.option("--fix", is_flag=True, help="Auto-fix issues where possible.")
def lint(fix):
    """Run health checks on the wiki."""
    kb_dir = _find_kb_dir()
    if kb_dir is None:
        click.echo("Error: Knowledge base not initialized. Run 'okb init' first.")
        raise SystemExit(1)

    from openkb.lint import run_structural_lint

    click.echo("Running structural checks...")
    structural_report = run_structural_lint(kb_dir)
    click.echo(structural_report)

    config = load_config(kb_dir / ".okb" / "config.yaml")
    click.echo("\nRunning knowledge checks...")
    try:
        from openkb.agent.linter import run_knowledge_lint

        knowledge_report = asyncio.run(run_knowledge_lint(kb_dir, config["model"]))
        click.echo(knowledge_report)
    except Exception as e:
        click.echo(f"Knowledge lint error: {e}")
        knowledge_report = ""

    # Write report
    from datetime import date

    report_dir = kb_dir / "wiki" / "reports"
    report_dir.mkdir(exist_ok=True)
    report_path = report_dir / f"lint-{date.today().isoformat()}.md"
    report_content = (
        f"# Lint Report — {date.today().isoformat()}\n\n"
        f"{structural_report}\n\n"
        f"## Knowledge Issues\n\n{knowledge_report}\n"
    )
    report_path.write_text(report_content, encoding="utf-8")
    click.echo(f"\nReport saved to {report_path}")

    if fix:
        click.echo("\n--fix: Auto-fixing structural issues is not yet implemented.")
```

- [ ] **Step 4: Run lint tests — should pass**

Run: `pytest tests/test_lint.py -v`
Expected: All passed (including new agent tests).

- [ ] **Step 5: Commit**

```bash
git add openkb/agent/linter.py openkb/cli.py tests/test_lint.py
git commit -m "feat: knowledge lint agent and okb lint command"
```

---

## Task 15: okb list + okb status

**Files:**
- Modify: `openkb/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write tests**

```python
# Append to tests/test_cli.py

def test_list_command(tmp_path):
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path) as td:
        runner.invoke(cli, ["init"])
        base = Path(td)
        # Add fake entries to hashes.json
        import json
        hashes = {
            "aaa": {"name": "paper.pdf", "type": "short", "pages": 12},
            "bbb": {"name": "textbook.pdf", "type": "pageindex", "pages": 520},
        }
        (base / ".okb" / "hashes.json").write_text(json.dumps(hashes))
        # Add a concept
        (base / "wiki" / "concepts" / "attention.md").write_text(
            "---\nsources: [paper.pdf, textbook.pdf]\n---\nContent."
        )

        result = runner.invoke(cli, ["list"])
        assert result.exit_code == 0
        assert "paper.pdf" in result.output
        assert "textbook.pdf" in result.output
        assert "attention.md" in result.output


def test_status_command(tmp_path):
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path) as td:
        runner.invoke(cli, ["init"])
        base = Path(td)
        import json
        hashes = {"aaa": {"name": "paper.pdf", "type": "short"}}
        (base / ".okb" / "hashes.json").write_text(json.dumps(hashes))
        (base / "wiki" / "sources" / "paper.md").write_text("src")
        (base / "wiki" / "summaries" / "paper.md").write_text("sum")

        result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0
        assert "Documents:" in result.output or "1" in result.output
```

- [ ] **Step 2: Run tests — should fail**

Run: `pytest tests/test_cli.py::test_list_command -v`
Expected: FAIL

- [ ] **Step 3: Implement list and status commands**

Replace the `list_docs` and `status` commands in `openkb/cli.py`:

```python
@cli.command(name="list")
def list_docs():
    """List indexed documents."""
    kb_dir = _find_kb_dir()
    if kb_dir is None:
        click.echo("Error: Knowledge base not initialized. Run 'okb init' first.")
        raise SystemExit(1)

    registry = __import__("openkb.state", fromlist=["HashRegistry"]).HashRegistry(
        kb_dir / ".okb" / "hashes.json"
    )
    entries = registry.all_entries()

    docs = [v for v in entries.values()]
    if not docs:
        click.echo("No documents indexed.")
        return

    click.echo(f"Documents ({len(docs)}):")
    for doc in sorted(docs, key=lambda d: d["name"]):
        doc_type = doc.get("type", "short")
        pages = doc.get("pages", "")
        pages_str = f"{pages} pages" if pages else "—"
        click.echo(f"  {doc['name']:<30} {doc_type:<12} {pages_str}")

    # List concepts
    concepts_dir = kb_dir / "wiki" / "concepts"
    if concepts_dir.is_dir():
        concepts = sorted(f.name for f in concepts_dir.iterdir() if f.suffix == ".md")
        if concepts:
            click.echo(f"\nConcepts ({len(concepts)}):")
            for c in concepts:
                click.echo(f"  {c}")


@cli.command()
def status():
    """Show knowledge base status."""
    kb_dir = _find_kb_dir()
    if kb_dir is None:
        click.echo("Error: Knowledge base not initialized. Run 'okb init' first.")
        raise SystemExit(1)

    registry = __import__("openkb.state", fromlist=["HashRegistry"]).HashRegistry(
        kb_dir / ".okb" / "hashes.json"
    )
    entries = registry.all_entries()
    short_count = sum(1 for v in entries.values() if v.get("type") == "short")
    pi_count = sum(1 for v in entries.values() if v.get("type") == "pageindex")

    wiki = kb_dir / "wiki"
    sources = len(list((wiki / "sources").glob("*.md"))) if (wiki / "sources").is_dir() else 0
    summaries = len(list((wiki / "summaries").glob("*.md"))) if (wiki / "summaries").is_dir() else 0
    concepts = len(list((wiki / "concepts").glob("*.md"))) if (wiki / "concepts").is_dir() else 0
    total = sources + summaries + concepts + (1 if (wiki / "index.md").exists() else 0)

    click.echo(f"Knowledge Base: {kb_dir.name}/")
    click.echo(f"  Documents:    {len(entries)} ({short_count} short, {pi_count} pageindex)")
    click.echo(f"  Sources:      {sources} files")
    click.echo(f"  Summaries:    {summaries} files")
    click.echo(f"  Concepts:     {concepts} files")
    click.echo(f"  Wiki pages:   {total} total")
```

- [ ] **Step 4: Run tests — should pass**

Run: `pytest tests/test_cli.py -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add openkb/cli.py tests/test_cli.py
git commit -m "feat: implement okb list and okb status commands"
```

---

## Task 16: Final Integration Test + Cleanup

**Files:**
- Modify: `tests/test_cli.py`
- Create: `tests/fixtures/short.pdf` (or skip PDF fixture for now)

- [ ] **Step 1: Write end-to-end smoke test**

```python
# Append to tests/test_cli.py

def test_full_workflow_markdown(tmp_path):
    """Smoke test: init → add .md → list → status."""
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path) as td:
        base = Path(td)
        runner.invoke(cli, ["init"])

        # Create a markdown file
        md_file = base / "test_doc.md"
        md_file.write_text("# Test Document\n\nThis is about transformers and attention.\n")

        # Add it (will fail at LLM compilation but should not crash)
        with patch("openkb.cli._add_single_file") as mock_add:
            mock_add.return_value = None
            result = runner.invoke(cli, ["add", str(md_file)])
            assert result.exit_code == 0

        # List should work even with no docs indexed
        result = runner.invoke(cli, ["list"])
        assert result.exit_code == 0

        # Status should work
        result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0
        assert "Knowledge Base" in result.output
```

- [ ] **Step 2: Run full test suite**

Run: `pytest -v`
Expected: All tests pass.

- [ ] **Step 3: Verify CLI works end-to-end**

Run:
```bash
cd /tmp && mkdir test-kb && cd test-kb
okb init
okb status
okb list
```
Expected: init creates structure, status shows 0 documents, list shows nothing.

- [ ] **Step 4: Commit**

```bash
git add tests/
git commit -m "test: add integration smoke test"
```

---

## Summary

| Task | Component | Key Files |
|------|-----------|-----------|
| 1 | Project scaffold | pyproject.toml, cli.py, conftest.py |
| 2 | Config + state | config.py, state.py, schema.py |
| 3 | okb init | cli.py |
| 4 | Image extraction | images.py |
| 5 | Converter | converter.py |
| 6 | Tree renderer | tree_renderer.py |
| 7 | PageIndex indexer | indexer.py |
| 8 | Agent tools | agent/tools.py |
| 9 | Wiki compiler agent | agent/compiler.py |
| 10 | okb add orchestrator | cli.py |
| 11 | Q&A agent | agent/query.py |
| 12 | Watch mode | watcher.py |
| 13 | Structural lint | lint.py |
| 14 | Knowledge lint + okb lint | agent/linter.py, cli.py |
| 15 | okb list + okb status | cli.py |
| 16 | Integration test | tests/ |
