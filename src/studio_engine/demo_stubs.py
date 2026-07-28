"""Day 3 demo-only collaborators — Day 6 replaces with real composition in
`apps/studio`.

These 4 classes exist so the 4 filled node-executor bodies (`executors.py`)
can run standalone for a demo/test without any real KB/LLM/embedding/tool
backend wired up: `EmptyKbSearch` (always `[]`), `FixtureLLM` (VCR-style
replay of a recorded fixture), `EmptyEmbedding` (always `[]` — required only
because `LlmStepExecutor.__init__` takes an `EmbeddingService` collaborator,
even though Day 3 never calls it for anything real), `WhitelistToolDispatch`
(stub tool dispatcher, raises outside its whitelist). None of this is
production behavior — it is scaffolding to make the interpreter demo-able
before the real quadrant impls (KB/LLM/embedding/tool) land.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from studio_contracts import KbSearchResultItem

_FIXTURES_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "llm_step"


class EmptyKbSearch:
    """Day 3 demo-only — Day 6 replaces with real composition in
    `apps/studio`. Satisfies `studio_contracts.KbSearch`; always returns `[]`
    (the real `packages/kb` impl lands later)."""

    async def search(
        self,
        query: str,
        tenant_id: UUID,
        section_roles: list[str],
        top_k: int,
    ) -> list[KbSearchResultItem]:
        del query, tenant_id, section_roles, top_k
        return []


class FixtureLLM:
    """Day 3 demo-only — Day 6 replaces with real composition in
    `apps/studio`. Satisfies `studio_contracts.LLM`; replays the recorded
    `response` field from `tests/fixtures/llm_step/<case_id>.json` (VCR-style,
    see `packages/engine/README.md` "Fixture format"). The fixtures dir is
    resolved relative to `Path(__file__)` (not cwd) so this works identically
    from pytest and a CLI invocation.
    """

    def __init__(self, case_id: str) -> None:
        self._case_id = case_id

    async def complete(self, prompt: str, **kwargs: object) -> str:
        del prompt, kwargs
        fixture_path = _FIXTURES_DIR / f"{self._case_id}.json"
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
        return str(data["response"])


class EmptyEmbedding:
    """Day 3 demo-only — Day 6 replaces with real composition in
    `apps/studio`. Satisfies `studio_contracts.EmbeddingService`; always
    returns `[]` (real impl is Day 7)."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        del texts
        return []


_EMBED_FIXTURES_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "embedding"


class StubEmbedding:
    """Day 7 deliverable — the CI-blessed stub-local impl of `EmbeddingService`
    (`protocols.py`'s "2 impl expected: stub local fixtures (CI, 100%) +
    gateway"). VCR-style replay of `tests/fixtures/embedding/<case_id>.json`
    (same convention as `FixtureLLM` above): `embed()` ignores each text's
    CONTENT and replays the recorded `response` vectors, cycling through them
    to always return exactly `len(texts)` vectors — `EmbeddingService.embed`'s
    implicit invariant (1 vector per input text, same as the real
    `apps/studio/providers/fakes.py::FakeEmbedding` double) still holds even
    though this stub is not content-aware. No model/network call. Vector
    width is fixed at 8, matching
    `packages/kb/src/studio_kb/schema.py::EMBEDDING_DIM` (documented
    invariant, not imported — `.importlinter` forbids `studio_engine`
    importing `studio_kb`); re-pin both constants together if that width
    ever changes.
    """

    def __init__(self, case_id: str) -> None:
        self._case_id = case_id

    async def embed(self, texts: list[str]) -> list[list[float]]:
        fixture_path = _EMBED_FIXTURES_DIR / f"{self._case_id}.json"
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
        vectors = data["response"]
        return [list(vectors[i % len(vectors)]) for i in range(len(texts))]


class WhitelistToolDispatch:
    """Day 3 demo-only — Day 6 replaces with real composition in
    `apps/studio`. Stub `tool-call` dispatcher: raises `ValueError` for a
    tool outside `whitelist`, else returns the stub-dispatched marker
    consumed as-is by `ToolCallExecutor.execute` (defense-in-depth alongside
    the recipe-validator layer, see `executors.py::ToolCallExecutor`).
    """

    def __init__(self, whitelist: list[str]) -> None:
        self._whitelist = whitelist

    async def dispatch(self, tool: str) -> object:
        if tool not in self._whitelist:
            raise ValueError(f"tool not in whitelist: {tool}")
        return {"tool": tool, "status": "stub-dispatched"}
