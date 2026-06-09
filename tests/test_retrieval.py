from openkb.retrieval import select_relevant_briefs


def _block(*lines: str) -> str:
    return "\n".join(lines)


def test_none_and_empty_passthrough():
    assert select_relevant_briefs("q", "(none yet)", 5) == "(none yet)"
    assert select_relevant_briefs("q", "", 5) == ""


def test_under_budget_is_unchanged():
    block = _block("- a: alpha", "- b: beta")
    assert select_relevant_briefs("anything", block, 5) == block


def test_selects_relevant_top_k():
    block = _block(
        "- agent-swarm: spawning many sub-agents for parallel code generation",
        "- retirement-funds: using 401k money to fund a business tax-free",
        "- prompt-caching: reusing cached prompt prefixes to cut token cost",
    )
    out = select_relevant_briefs(
        "A workflow that spawns parallel sub-agents to generate code.", block, 1
    )
    assert out == "- agent-swarm: spawning many sub-agents for parallel code generation"
    assert len(out.splitlines()) == 1


def test_preserves_original_order_among_kept():
    block = _block("- one: alpha beta", "- two: beta gamma", "- three: gamma delta")
    out = select_relevant_briefs("beta gamma delta", block, 2)
    lines = out.splitlines()
    # whichever two are kept, they appear in their original relative order
    assert lines == [l for l in block.splitlines() if l in lines]
    assert len(lines) == 2
