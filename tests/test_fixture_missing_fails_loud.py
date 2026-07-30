"""Day 9 negative tests (AIE-1, issue #42): a fixture that is missing or
malformed must fail **clearly** and must never be swallowed.

DoD, verbatim: *"negative: fixture thiếu → fail **rõ**, không nuốt lỗi"*, under
the day's constraint that a negative test has to catch a REAL error, not be a
fake test (`docs/requirements/week-1/days/day-09.md`).

Measured before this file existed (OBSERVED, not reasoned):

- `FixtureLLM("nope").complete(...)` → bare `FileNotFoundError` whose only
  content was an ABSOLUTE host path
  (`C:\\Users\\<name>\\...\\tests\\fixtures\\llm_step\\nope.json`) — loud, but it
  named neither the case_id nor the fixture convention, and it leaked a local
  path into CI logs.
- `{"case_id": ...}` with no `"response"` → bare `KeyError: 'response'`.
- `FixtureLLM`'s `str(data["response"])` COERCED a non-string `response`
  (a dict became the literal text `"{'a': 1}"`) — a wrong fixture passed
  through silently, which is exactly "nuốt lỗi".
- `StubEmbedding` with `{"response": []}` → `ZeroDivisionError` from
  `i % len(vectors)` — an error with no visible connection to fixtures at all.

Every test below pins the FIXED shape (`FixtureError`, naming case_id +
repo-relative path). Three of them additionally assert the OLD failure mode is
gone (`not KeyError`, `not ZeroDivisionError`, `not "{'a': 1}"`), so a revert
cannot leave this file green.

Teeth (`docs/code-standards.md` §4.1, the `test_leak_meta.py` positive-teeth
pattern): `test_real_case_id_still_replays` and
`test_stub_embedding_still_replays_the_real_fixture` prove a stub that raised
`FixtureError` UNCONDITIONALLY would fail this suite — that is what makes the
negatives above real rather than vacuous.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from studio_contracts import (
    AgentConfig,
    Dag,
    Edge,
    KbBinding,
    Node,
    NodeType,
    Recipe,
    ScorecardThreshold,
    TraceEvent,
)
from studio_engine import demo_stubs, interpreter
from studio_engine.demo_stubs import EmptyEmbedding, EmptyKbSearch, FixtureError, FixtureLLM, StubEmbedding
from test_session_context_tenant_wall import ANKOR_ID, default_session_context

_MISSING_CASE_ID = "nope-does-not-exist"
_TOOL_NAME = "search_docs"
# Derived independently of `demo_stubs`' own private constant so the leak
# assertion below tests the MESSAGE, not a shared implementation detail.
_ENGINE_ROOT = Path(demo_stubs.__file__).resolve().parents[2]


class _RecordingTraceWriter:
    """Keeps every event handed to `write()` — lets a test prove exactly how
    far a run got before it died, which a no-op writer cannot show."""

    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    async def write(self, event: TraceEvent) -> None:
        self.events.append(event)


def _four_node_recipe() -> Recipe:
    """Same `kb-retrieve -> llm-step -> tool-call -> end` shape as
    `test_interpreter_behavior.py::_four_node_recipe` — the `llm-step` in the
    middle is the node whose fixture is missing, so the walk dies with one node
    already traced and two nodes still ahead of it."""
    return Recipe(
        agent_id="agent-1",
        tenant_id=ANKOR_ID,
        agent_config=AgentConfig(instructions="x", model="m", tool_whitelist=[_TOOL_NAME]),
        dag=Dag(
            nodes=[
                Node(id="n_kb", type=NodeType.KB_RETRIEVE, params={}),
                Node(id="n_llm", type=NodeType.LLM_STEP, params={}),
                Node(id="n_tool", type=NodeType.TOOL_CALL, params={"tool": _TOOL_NAME}),
                Node(id="n_end", type=NodeType.END, params={}),
            ],
            edges=[
                Edge(from_="n_kb", to="n_llm"),
                Edge(from_="n_llm", to="n_tool"),
                Edge(from_="n_tool", to="n_end"),
            ],
        ),
        kb_binding=KbBinding(kb_id="kb-1", scope="ankor/public"),
        golden_set_ref="golden-1",
        scorecard_threshold=ScorecardThreshold(success=0.8, citation_accuracy=0.8),
    )


def _write_llm_fixture(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, case_id: str, body: str) -> None:
    """Point `FixtureLLM` at a throwaway dir and plant a deliberately broken
    fixture there. Redirecting the module global (read at call time) keeps the
    broken bytes OUT of the committed `tests/fixtures/` tree — a committed
    broken fixture would be indistinguishable from a real recorded one to the
    next reader — and avoids widening a production constructor just for tests."""
    monkeypatch.setattr(demo_stubs, "_FIXTURES_DIR", tmp_path)
    (tmp_path / f"{case_id}.json").write_text(body, encoding="utf-8")


def _write_embedding_fixture(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, case_id: str, body: str) -> None:
    monkeypatch.setattr(demo_stubs, "_EMBED_FIXTURES_DIR", tmp_path)
    (tmp_path / f"{case_id}.json").write_text(body, encoding="utf-8")


async def test_missing_llm_fixture_names_case_id_and_relative_path() -> None:
    """"Fail rõ" is concrete, not a vibe: the message must carry the case_id
    the caller asked for AND the repo-relative path where the recording was
    expected. Without the case_id, a CI log tells you a file is missing but not
    which replay wanted it."""
    with pytest.raises(FixtureError) as excinfo:
        await FixtureLLM(_MISSING_CASE_ID).complete("x")

    message = str(excinfo.value)
    assert _MISSING_CASE_ID in message
    assert f"tests/fixtures/llm_step/{_MISSING_CASE_ID}.json" in message


async def test_missing_fixture_message_does_not_leak_absolute_host_path() -> None:
    """The pre-Day-9 `FileNotFoundError` printed an absolute host path
    (`C:\\Users\\<name>\\...`). Repo-relative instead: identical message on
    every machine and in CI, so the assertion above is machine-independent and
    no local directory layout ends up in a shared log."""
    with pytest.raises(FixtureError) as excinfo:
        await FixtureLLM(_MISSING_CASE_ID).complete("x")

    assert str(_ENGINE_ROOT) not in str(excinfo.value)


async def test_real_case_id_still_replays() -> None:
    """POSITIVE TEETH — the anti-fake-test half of this file. A `FixtureLLM`
    that raised `FixtureError` unconditionally would satisfy every negative
    test above; it fails here. Pins against the fixture file's own bytes, not
    a duplicated literal."""
    fixture_path = _ENGINE_ROOT / "tests" / "fixtures" / "llm_step" / "smoke-01.json"
    expected = json.loads(fixture_path.read_text(encoding="utf-8"))["response"]

    answer = await FixtureLLM("smoke-01").complete("x")

    assert answer == expected
    assert isinstance(answer, str)


async def test_interpreter_does_not_swallow_a_missing_fixture() -> None:
    """"Không nuốt lỗi" at the INTERPRETER layer, which is the level that
    matters: `run()` has no `try/except` anywhere, so a fixture error must
    surface to the caller unchanged and no `RunResult` may come back.

    Also pins how far the run got: exactly ONE trace event (`n_kb`, written
    before `llm-step` was dispatched), and no `end` event. A truncated run must
    stay distinguishable from a completed one — `RunResult` carries no
    "terminated" flag, so if `run()` ever caught this and returned normally,
    evalhub would score a dead run as a finished one."""
    trace_writer = _RecordingTraceWriter()

    with pytest.raises(FixtureError, match="no-such-case"):
        await interpreter.run(
            _four_node_recipe(),
            session_context=default_session_context(),
            kb_search=EmptyKbSearch(),
            llm=FixtureLLM("no-such-case"),
            embedding=EmptyEmbedding(),
            trace_writer=trace_writer,
        )

    assert [event.node_id for event in trace_writer.events] == ["n_kb"]
    assert NodeType.END not in [event.node_type for event in trace_writer.events]


async def test_fixture_missing_response_key_fails_loud(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A recording with no `response` used to be a bare `KeyError: 'response'`
    — technically loud, but it named neither the fixture nor the file. Asserts
    `not KeyError` explicitly so reverting the guard cannot leave this green."""
    _write_llm_fixture(monkeypatch, tmp_path, "no-response", '{"case_id": "no-response"}')

    with pytest.raises(FixtureError) as excinfo:
        await FixtureLLM("no-response").complete("x")

    assert not isinstance(excinfo.value, KeyError)
    assert "response" in str(excinfo.value)
    assert "no-response" in str(excinfo.value)


async def test_unparseable_fixture_json_fails_loud(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A half-written / merge-mangled fixture is a realistic failure (these
    files are hand-edited). `json.JSONDecodeError` alone says "line 1 column 1"
    with no filename attached."""
    _write_llm_fixture(monkeypatch, tmp_path, "broken-json", "{not json at all")

    with pytest.raises(FixtureError) as excinfo:
        await FixtureLLM("broken-json").complete("x")

    assert "broken-json" in str(excinfo.value)
    assert "JSON" in str(excinfo.value)


async def test_llm_response_of_wrong_type_fails_instead_of_str_coercion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The worst of the four: `str(data["response"])` swallowed a wrong-typed
    `response` outright — a dict came back as the literal text `"{'a': 1}"`
    and the run CONTINUED, sending that garbage downstream as a real LLM
    answer. `LLM.complete` is typed `-> str`; a non-string recording is a
    broken recording, not something to stringify.

    Note the RAISE itself is the regression guard: if `str()`-coercion came
    back, `complete()` would return `"{'a': 1}"` normally and this test fails
    with "DID NOT RAISE" — no separate assertion needed for the old behavior."""
    _write_llm_fixture(monkeypatch, tmp_path, "dict-response", '{"case_id": "dict-response", "response": {"a": 1}}')

    with pytest.raises(FixtureError) as excinfo:
        await FixtureLLM("dict-response").complete("x")

    assert "dict-response" in str(excinfo.value)
    assert "dict" in str(excinfo.value)


async def test_missing_embedding_fixture_fails_loud() -> None:
    """`StubEmbedding` is the second fixture-backed replay (Day 7) and shares
    `FixtureLLM`'s convention, so it must share the failure contract too —
    otherwise "fixture thiếu fail rõ" holds on one replay path and not the
    other."""
    with pytest.raises(FixtureError) as excinfo:
        await StubEmbedding(_MISSING_CASE_ID).embed(["a"])

    message = str(excinfo.value)
    assert _MISSING_CASE_ID in message
    assert f"tests/fixtures/embedding/{_MISSING_CASE_ID}.json" in message


async def test_empty_embedding_response_fails_loud_not_zero_division(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`{"response": []}` hit `i % len(vectors)` → `ZeroDivisionError`, an
    error with no visible link to fixtures at all: whoever reads that traceback
    starts debugging arithmetic, not the recording. Asserts `not
    ZeroDivisionError` so the guard cannot be reverted silently."""
    _write_embedding_fixture(monkeypatch, tmp_path, "empty-vectors", '{"case_id": "empty-vectors", "response": []}')

    with pytest.raises(FixtureError) as excinfo:
        await StubEmbedding("empty-vectors").embed(["a"])

    assert not isinstance(excinfo.value, ZeroDivisionError)
    assert "empty-vectors" in str(excinfo.value)


async def test_stub_embedding_still_replays_the_real_fixture() -> None:
    """POSITIVE TEETH for the embedding half — same role as
    `test_real_case_id_still_replays` above. Cardinality (1 vector per input
    text) is `EmbeddingService.embed`'s invariant and must survive the guard."""
    vectors = await StubEmbedding("smoke-01").embed(["a", "b", "c"])

    assert len(vectors) == 3
    assert all(len(vector) == 8 for vector in vectors)


async def test_fixture_error_is_catchable_as_one_type() -> None:
    """All four failure modes must share ONE catchable type — that is the whole
    reason `FixtureError` exists rather than leaving every caller to catch
    `FileNotFoundError`/`KeyError`/`TypeError`/`ZeroDivisionError` separately.
    `RuntimeError` as the base is deliberate: none of the four belongs to a
    single builtin family, so no builtin base could cover all of them."""
    assert issubclass(FixtureError, RuntimeError)
