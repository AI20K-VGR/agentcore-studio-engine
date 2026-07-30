"""Day 9 happy-path tests (AIE-1, issue #42): the 3-node interpreter walk
replayed through fixtures must be DETERMINISTIC — *"Test interpreter 3-node
deterministic qua fixture"*, and the day's DoD *"Bảng điểm chạy lại ra cùng
số"* rests on it (a scorecard cannot be rerun-stable if the run underneath it
is not).

"Deterministic" is made precise here rather than left as a slogan. Two runs of
the same recipe must agree on everything CONTENT-bearing (every node output,
every trace field the scorecard reads, every `inputs_hash`) and must NOT agree
on the three IDENTITY/TIME fields that are fresh by design (`run_id`,
`event_id`, `ts` — `interpreter.py` mints them from `uuid4()`/`datetime.now`).
A test that asserted whole-`TraceEvent` equality would be asserting something
false; a test that asserted nothing about the identity fields would let a
cached-run bug (both calls returning the same run) pass as determinism.
`test_run_identity_fields_are_fresh_per_run` closes that second gap.

Why `_GroundedKbSearch` instead of the `EmptyKbSearch` every pre-Day-9 test
uses: with empty retrieval, `citations` is always `[]` and `refused` always
`True` (grounding is required — `executors.py::LlmStepExecutor`), so the
interesting numbers are structurally constant and their stability proves
nothing. This double returns `chunk-001`, the id the recorded `smoke-01.json`
answer actually brackets, so the walk reaches `citations == ["chunk-001"]` /
`refused is False` — the path `citation_accuracy` (AIE-2, read from the trace)
is scored on.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from studio_contracts import (
    AgentConfig,
    Dag,
    Edge,
    KbBinding,
    KbSearchResultItem,
    Node,
    NodeType,
    Recipe,
    ScorecardThreshold,
    TraceEvent,
)
from studio_engine import interpreter
from studio_engine.demo_stubs import EmptyEmbedding, FixtureLLM
from test_session_context_tenant_wall import ANKOR_ID, default_session_context

_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "llm_step" / "smoke-01.json"
_TOOL_NAME = "search_docs"
# The id the recorded `smoke-01.json` answer brackets — retrieval has to return
# THIS id for the citation to be grounded (both retrieved and referenced).
_GROUNDED_CHUNK_ID = "chunk-001"


class _GroundedKbSearch:
    """Returns exactly the one chunk the recorded answer cites, so the walk
    exercises the non-empty `citations` branch. Content is fixed per call — the
    point of the suite is that the INTERPRETER adds no variance, so the
    collaborators must not add any either."""

    async def search(
        self,
        query: str,
        tenant_id: UUID,
        section_roles: list[str],
        top_k: int,
    ) -> list[KbSearchResultItem]:
        del query, section_roles, top_k
        return [
            KbSearchResultItem(
                chunk_id=_GROUNDED_CHUNK_ID,
                text="Nhân viên tenant ankor được nghỉ phép năm 12 ngày.",
                score=0.91,
                tenant_id=tenant_id,
                section_role="public",
            )
        ]


class _RecordingTraceWriter:
    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    async def write(self, event: TraceEvent) -> None:
        self.events.append(event)


def _four_node_recipe() -> Recipe:
    """`kb-retrieve -> llm-step -> tool-call -> end` — the 3 hardcoded Week-1
    node types plus the terminal `end`, same shape as
    `test_interpreter_behavior.py::_four_node_recipe`."""
    return Recipe(
        agent_id="agent-1",
        tenant_id=ANKOR_ID,
        agent_config=AgentConfig(instructions="Trả lời ngắn gọn.", model="m", tool_whitelist=[_TOOL_NAME]),
        dag=Dag(
            nodes=[
                Node(id="n_kb", type=NodeType.KB_RETRIEVE, params={"query": "nghỉ phép bao nhiêu ngày", "top_k": 3}),
                Node(id="n_llm", type=NodeType.LLM_STEP, params={"temperature": 0.0}),
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


async def _run(trace_writer: _RecordingTraceWriter) -> interpreter.RunResult:
    """One full walk. Every collaborator is constructed FRESH per call — a
    shared instance could hide state carried between runs, which is exactly
    what these tests are trying to detect."""
    return await interpreter.run(
        _four_node_recipe(),
        session_context=default_session_context(),
        kb_search=_GroundedKbSearch(),
        llm=FixtureLLM("smoke-01"),
        embedding=EmptyEmbedding(),
        trace_writer=trace_writer,
    )


def _content_projection(events: list[TraceEvent]) -> list[dict[str, object]]:
    """Every trace field EXCEPT the three that are fresh by design
    (`run_id`, `event_id`, `ts`). Includes `inputs_hash`, which is the field
    most likely to go non-deterministic by accident: `interpreter.py` builds it
    with `json.dumps(node.params, sort_keys=True, default=str)`, so any param
    whose `str()` varies per run (an object with a default `repr`, a set, a
    timestamp) silently makes two identical runs hash differently — and
    `inputs_hash` is what tells DE's trace store two runs had the same input."""
    return [
        {
            "node_id": event.node_id,
            "node_type": event.node_type,
            "agent_id": event.agent_id,
            "tenant_id": event.tenant_id,
            "inputs_hash": event.inputs_hash,
            "outputs": event.outputs,
            "tokens": event.tokens,
            "cost": event.cost,
            "citations": event.citations,
        }
        for event in events
    ]


async def test_repeat_run_yields_identical_final_state() -> None:
    """Same recipe, twice → identical `final_state`.

    The `==` alone would pass on a degenerate implementation that returned
    empty output for every node, so the concrete values are pinned too: the
    replayed answer, the grounded citation, `refused is False`, and both
    downstream node outputs."""
    first = await _run(_RecordingTraceWriter())
    second = await _run(_RecordingTraceWriter())

    assert first.final_state == second.final_state

    expected_answer = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))["response"]
    for result in (first, second):
        assert list(result.final_state.keys()) == ["n_kb", "n_llm", "n_tool", "n_end"]
        llm_output = result.final_state["n_llm"]
        assert isinstance(llm_output, dict)
        assert llm_output["answer"] == expected_answer
        assert llm_output["citations"] == [_GROUNDED_CHUNK_ID]
        assert llm_output["refused"] is False
        assert result.final_state["n_tool"] == {"tool": _TOOL_NAME, "status": "stub-dispatched"}
        assert result.final_state["n_end"] == {"terminated": True}


async def test_repeat_run_yields_identical_trace_projection() -> None:
    """The trace is the artifact other quadrants consume (DE stores it, AIE-2
    scores `citation_accuracy` off it), so its content — not just `run()`'s
    return value — has to be reproducible.

    Guards against a vacuous pass: the projection must actually hold 4 events
    and every `inputs_hash` must be a non-empty string, so an empty or
    all-`None` projection cannot satisfy the equality."""
    first_writer = _RecordingTraceWriter()
    second_writer = _RecordingTraceWriter()
    await _run(first_writer)
    await _run(second_writer)

    first_projection = _content_projection(first_writer.events)
    second_projection = _content_projection(second_writer.events)

    assert len(first_projection) == 4
    assert first_projection == second_projection
    for entry in first_projection:
        inputs_hash = entry["inputs_hash"]
        assert isinstance(inputs_hash, str)
        assert inputs_hash != ""
    # Pinned separately from the projection equality: this is the one field
    # whose stability depends on `node.params` serialization rather than on the
    # executors, and a walk-wide hash collision would make the check above
    # pass while telling DE every node had the same input.
    assert len({str(entry["inputs_hash"]) for entry in first_projection}) == 4


async def test_run_identity_fields_are_fresh_per_run() -> None:
    """The other half of a precise determinism claim: content repeats,
    IDENTITY does not. Two runs must be two distinct runs — same `run_id` or
    reused `event_id`s would mean a cached/replayed run, which would make the
    equality assertions above true for the wrong reason."""
    first_writer = _RecordingTraceWriter()
    second_writer = _RecordingTraceWriter()
    first = await _run(first_writer)
    second = await _run(second_writer)

    assert first.run_id != second.run_id
    all_event_ids = [event.event_id for event in first_writer.events + second_writer.events]
    assert len(set(all_event_ids)) == len(all_event_ids) == 8
    for writer in (first_writer, second_writer):
        timestamps = [event.ts for event in writer.events]
        assert timestamps == sorted(timestamps)
        assert len(set(timestamps)) == 4


async def test_answer_comes_from_the_fixture_file_not_a_hardcoded_string() -> None:
    """Where the determinism actually comes from: the recorded fixture, not
    coincidence. Compares against the bytes on disk, so editing
    `tests/fixtures/llm_step/smoke-01.json` moves this assertion with it
    instead of leaving a stale literal duplicated in the test."""
    recorded = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    result = await _run(_RecordingTraceWriter())

    llm_output = result.final_state["n_llm"]
    assert isinstance(llm_output, dict)
    assert llm_output["answer"] == recorded["response"]
    assert f"[{_GROUNDED_CHUNK_ID}]" in str(recorded["response"])
