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

_ENGINE_ROOT = Path(__file__).resolve().parents[2]
_FIXTURES_DIR = _ENGINE_ROOT / "tests" / "fixtures" / "llm_step"


class FixtureError(RuntimeError):
    """A VCR fixture could not be replayed — missing file, unparseable JSON,
    missing `response` key, or a `response` of the wrong type (Day 9 harden,
    spec AIE-1, issue #42).

    ONE type for all four because they do not share a builtin base: before
    this, the four failed as `FileNotFoundError` / `json.JSONDecodeError` /
    `KeyError` / (for `StubEmbedding`) `ZeroDivisionError`, so no caller could
    catch "the fixture is broken" as a single condition, and two of the four
    said nothing about fixtures at all. Every message names the fixture family,
    the `case_id`, and the repo-relative path.

    Fail-closed, never fall back: CI replays recorded fixtures only (INV-4
    fixtures-first — no live model call is permitted), so a missing recording
    means the run cannot produce a real answer. Substituting a placeholder here
    would make an unreplayable case look like a normal (and deterministic!)
    run to `interpreter.run()`, to the trace, and therefore to evalhub's
    scorecard.
    """


def _rel(path: Path) -> str:
    """`path` relative to the engine package root, POSIX-style — the form that
    goes into a `FixtureError` message.

    Repo-relative, not absolute: the pre-Day-9 `FileNotFoundError` printed the
    whole host path (`C:\\Users\\<name>\\...\\tests\\fixtures\\llm_step\\x.json`),
    which both leaked a local directory layout into shared CI logs and made the
    message differ per machine (so no test could assert on it). Falls back to
    the bare filename when `path` lies outside the package — that happens only
    when a caller (or a test) has redirected the fixtures dir elsewhere, and
    the `case_id` in the message already identifies the recording.
    """
    try:
        return path.resolve().relative_to(_ENGINE_ROOT).as_posix()
    except ValueError:
        return path.name


def _load_fixture(kind: str, fixtures_dir: Path, case_id: str, required_key: str = "response") -> dict[str, object]:
    """Read + parse `<fixtures_dir>/<case_id>.json` and guarantee
    `required_key` exists. Shared by all replay stubs so the fixture families
    (`llm_step`, `embedding`, and — engine#33 phase 3 — `agent_loop`, whose
    multi-turn recordings key off `"responses"` rather than singular
    `"response"`) cannot drift apart on failure behavior — the caller only has
    to type-check its own key for its own protocol.

    `raise ... from exc` throughout: the underlying OSError/JSONDecodeError
    stays on the chain, so wrapping adds context without hiding the cause.
    """
    fixture_path = fixtures_dir / f"{case_id}.json"
    try:
        raw = fixture_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FixtureError(
            f"{kind} fixture {case_id!r} not found — expected {_rel(fixture_path)}. "
            "CI replays recorded fixtures only (INV-4 fixtures-first, no live model call), so a "
            'missing recording is a hard failure with no fallback; record it per README.md "Fixture format".'
        ) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FixtureError(f"{kind} fixture {case_id!r} at {_rel(fixture_path)} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise FixtureError(
            f"{kind} fixture {case_id!r} at {_rel(fixture_path)} must be a JSON object, got {type(data).__name__!r}."
        )
    if required_key not in data:
        raise FixtureError(
            f"{kind} fixture {case_id!r} at {_rel(fixture_path)} has no {required_key!r} key — that key holds "
            'the recorded return value being replayed; see README.md "Fixture format".'
        )
    return data


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

    Day 9 harden: an unreplayable fixture raises `FixtureError` (see
    `_load_fixture`) instead of a bare `FileNotFoundError`/`KeyError`.
    """

    def __init__(self, case_id: str) -> None:
        self._case_id = case_id

    async def complete(self, prompt: str, **kwargs: object) -> str:
        del prompt, kwargs
        data = _load_fixture("llm_step", _FIXTURES_DIR, self._case_id)
        response = data["response"]
        if not isinstance(response, str):
            # Was `str(data["response"])` before Day 9 — that COERCED a
            # wrong-typed recording (a dict came back as the literal text
            # "{'a': 1}") and the run continued, handing that string
            # downstream as a real LLM answer. `LLM.complete` is typed
            # `-> str`; a non-string recording is a broken recording.
            raise FixtureError(
                f"llm_step fixture {self._case_id!r} has a non-string 'response' "
                f"(got {type(response).__name__!r}) — `LLM.complete` is typed `-> str`, and "
                "stringifying a wrong-shaped recording would pass garbage downstream as a real answer."
            )
        return response


class EmptyEmbedding:
    """Day 3 demo-only — Day 6 replaces with real composition in
    `apps/studio`. Satisfies `studio_contracts.EmbeddingService`; always
    returns `[]` (real impl is Day 7)."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        del texts
        return []


_EMBED_FIXTURES_DIR = _ENGINE_ROOT / "tests" / "fixtures" / "embedding"


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

    Day 9 harden: an unreplayable fixture raises `FixtureError` (see
    `_load_fixture`), same contract as `FixtureLLM` above.
    """

    def __init__(self, case_id: str) -> None:
        self._case_id = case_id

    async def embed(self, texts: list[str]) -> list[list[float]]:
        data = _load_fixture("embedding", _EMBED_FIXTURES_DIR, self._case_id)
        vectors = data["response"]
        if not isinstance(vectors, list) or not vectors:
            # The `not vectors` half is why this guard exists: an empty list
            # reached `i % len(vectors)` below and died with a bare
            # `ZeroDivisionError` — an error with no visible connection to
            # fixtures, so whoever read the traceback started debugging
            # arithmetic instead of the recording.
            raise FixtureError(
                f"embedding fixture {self._case_id!r} needs a non-empty JSON list of vectors in "
                f"'response' (got {type(vectors).__name__!r}) — `embed()` cycles through them to "
                "return 1 vector per input text, which is impossible with none recorded."
            )
        return [list(vectors[i % len(vectors)]) for i in range(len(texts))]


_AGENT_LOOP_FIXTURES_DIR = _ENGINE_ROOT / "tests" / "fixtures" / "agent_loop"


class ToolCallingFixtureLLM:
    """engine#33 phase 3 (H3/R2) — VCR-style replay, same convention as
    `FixtureLLM` above, but of a MULTI-turn scripted conversation rather than
    a single answer: `tests/fixtures/agent_loop/<case_id>.json`'s `responses`
    is a list of strings, call N of `complete()` returns `responses[N]`.

    Exists specifically so `agent_loop.run_agent_loop()`'s tool-call branch is
    driven by a real, non-test-local, product-shipped `LLM` double — as of
    this phase every OTHER `LLM` impl in the workspace (`FixtureLLM` here,
    `_GoldenAwareLLM`, `ExtractiveFakeLLM`) only ever emits bare prose with
    `[chunk_id]` brackets, never a `TOOL_CALL:` signal, which would leave the
    tool-call branch reachable only from test-local doubles — an INERT
    feature under every real demo/CLI/manual run configuration (plan.md R2).

    Day 9 harden semantics carried over unchanged (`FixtureError`, see
    `_load_fixture`): calling past the recorded `responses` list is a FAIL
    LOUD `FixtureError`, never a silent repeat of the last element — a
    recording that cannot replay the exact conversation length asked of it is
    a broken recording, and silently looping would turn a genuine
    over-max_turns bug into a false-green run.

    Deliberately NOT added to `executors._KNOWN_STUB_LLM_CLASS_NAMES`
    (engine#33 phase 3 scope note): this loop does not emit `llm_source` at
    all (out of scope, see plan.md), and touching that set would change
    `LlmStepExecutor`'s behavior — i.e. `interpreter.run()`'s behavior — which
    is exactly what K2 forbids.
    """

    def __init__(self, case_id: str) -> None:
        self._case_id = case_id
        self._calls = 0

    async def complete(self, prompt: str, **kwargs: object) -> str:
        del prompt, kwargs
        data = _load_fixture("agent_loop", _AGENT_LOOP_FIXTURES_DIR, self._case_id, required_key="responses")
        raw_responses = data["responses"]
        if not isinstance(raw_responses, list) or not raw_responses:
            raise FixtureError(
                f"agent_loop fixture {self._case_id!r} needs a non-empty JSON list of strings in "
                f"'responses' (got {type(raw_responses).__name__!r}) — each element replays 1 turn of "
                "`LLM.complete()`."
            )
        # Narrow to `list[str]` explicitly (a generator-expression `all(...)`
        # check does not narrow the list's element type for mypy strict) — a
        # non-str element makes the filtered length shorter than the raw one.
        responses = [item for item in raw_responses if isinstance(item, str)]
        if len(responses) != len(raw_responses):
            raise FixtureError(
                f"agent_loop fixture {self._case_id!r} has a non-string element in 'responses' — every "
                "element must replay 1 `LLM.complete()` turn as a string."
            )
        if self._calls >= len(responses):
            raise FixtureError(
                f"agent_loop fixture {self._case_id!r} has only {len(responses)} recorded turn(s), "
                f"but complete() was called a {self._calls + 1}th time — a recording that cannot "
                "replay the exact conversation length asked of it is broken; this does NOT silently "
                "repeat the last element (that would mask a genuine over-max_turns bug as green)."
            )
        response = responses[self._calls]
        self._calls += 1
        return response


class WhitelistToolDispatch:
    """Real composition now lives one layer up, in `apps/studio`'s
    `RealToolDispatch` (`providers/tool_dispatch.py`, engine#32) — this
    class stays as `interpreter.run()`'s engine-internal FALLBACK dispatcher
    for callers that don't inject `tool_dispatch` (mainly `packages/engine`'s
    own tests; production composition roots always inject the real one).
    Stub `tool-call` dispatcher: raises `ValueError` for a tool outside
    `whitelist`, else returns the stub-dispatched marker consumed as-is by
    `ToolCallExecutor.execute` (defense-in-depth alongside the
    recipe-validator layer, see `executors.py::ToolCallExecutor`).
    """

    def __init__(self, whitelist: list[str]) -> None:
        self._whitelist = whitelist

    async def dispatch(self, tool: str, params: dict[str, object]) -> object:
        if tool not in self._whitelist:
            raise ValueError(f"tool not in whitelist: {tool}")
        return {"tool": tool, "status": "stub-dispatched"}
