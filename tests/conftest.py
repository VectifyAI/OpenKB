import json
import pytest


@pytest.fixture
def kb_dir(tmp_path):
    """Create a minimal knowledge base directory structure for testing."""
    # Raw documents
    (tmp_path / "raw").mkdir()

    # Wiki sub-directories
    (tmp_path / "wiki" / "sources" / "images").mkdir(parents=True)
    (tmp_path / "wiki" / "summaries").mkdir(parents=True)
    (tmp_path / "wiki" / "concepts").mkdir(parents=True)
    (tmp_path / "wiki" / "reports").mkdir(parents=True)

    # .okb state directory
    okb_dir = tmp_path / ".okb"
    okb_dir.mkdir()

    config_yaml = """\
version: "0.1.0"
embedding_model: text-embedding-3-small
llm_model: gpt-4o-mini
chunk_size: 512
chunk_overlap: 64
"""
    (okb_dir / "config.yaml").write_text(config_yaml)
    (okb_dir / "hashes.json").write_text(json.dumps({}))

    return tmp_path


@pytest.fixture
def sample_tree():
    """Return a sample PageIndex tree structure dict for testing."""
    return {
        "doc_name": "Sample Document",
        "doc_description": "A sample document used for unit testing.",
        "structure": [
            {
                "title": "Introduction",
                "node_id": "node-1",
                "start_index": 0,
                "end_index": 120,
                "summary": "Overview of the document topic.",
                "text": "This document introduces the core concepts of the system.",
                "nodes": [
                    {
                        "title": "Background",
                        "node_id": "node-1-1",
                        "start_index": 0,
                        "end_index": 60,
                        "summary": "Historical context.",
                        "text": "Background information on the subject.",
                        "nodes": [],
                    },
                    {
                        "title": "Motivation",
                        "node_id": "node-1-2",
                        "start_index": 61,
                        "end_index": 120,
                        "summary": "Why this work matters.",
                        "text": "Explanation of the motivation behind this work.",
                        "nodes": [],
                    },
                ],
            },
            {
                "title": "Conclusion",
                "node_id": "node-2",
                "start_index": 121,
                "end_index": 200,
                "summary": "Summary of findings.",
                "text": "The system performs well under the described conditions.",
                "nodes": [],
            },
        ],
    }
