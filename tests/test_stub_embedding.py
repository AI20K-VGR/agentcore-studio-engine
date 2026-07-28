"""Day 7 — `StubEmbedding` (spec AIE-1, R-SPEC A1#5). VCR-style replay of
`tests/fixtures/embedding/<case_id>.json`, mirroring `FixtureLLM`'s convention
(see `test_llm_step_replays_fixture_answer` in `test_executors_behavior.py`).
"""

from __future__ import annotations

from studio_engine.demo_stubs import StubEmbedding


async def test_stub_embedding_replays_fixture_vectors() -> None:
    """`embed()` ignores its `texts` argument and replays the recorded
    `response` vectors verbatim from `smoke-01.json` — no model/network call."""
    result = await StubEmbedding("smoke-01").embed(["a", "b"])

    assert result == [
        [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8],
        [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9],
    ]


async def test_stub_embedding_vectors_are_8_dimensional() -> None:
    """Vector width must match `packages/kb/src/studio_kb/schema.py::EMBEDDING_DIM`
    (documented invariant, not imported — `.importlinter` forbids `studio_engine`
    importing `studio_kb`)."""
    result = await StubEmbedding("smoke-01").embed(["anything"])

    assert all(len(vector) == 8 for vector in result)


async def test_stub_embedding_is_deterministic() -> None:
    """Same case_id replayed twice must yield the identical result — CI must
    never see a flaky/varying embedding stub."""
    first = await StubEmbedding("smoke-01").embed(["a", "b"])
    second = await StubEmbedding("smoke-01").embed(["a", "b"])

    assert first == second
