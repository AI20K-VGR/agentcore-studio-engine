"""Day 6 spine fix — `llm-step` must actually SEE the retrieved chunks.

Before this, `LlmStepExecutor` read `node.params["prompt"]` and nothing ever
wrote it: SWE's published recipe (`studio_workbench.builder_d4`) declares the
`llm-step` node as `params={"temperature": 0.0}` with no `prompt` key, so the
executor fell back to `""` and called the LLM with an EMPTY prompt. The
retrieved chunks were threaded in only to be used as a grounding *filter* —
they were never put in front of the model. Measured on the published recipe
before the fix: `llm prompt seen : ''`.

That made the whole citation chain unreachable end-to-end: no chunk in the
prompt → no `[chunk_id]` in the answer → grounding filter keeps nothing →
`citation_accuracy` 0 against SWE's `0.95` threshold.

Ownership: building this prompt is AIE-1's, not SWE's — `umbrella-contract.md:63`
assigns `llm-step` ("1 bước LLM ... embed/complete via EmbeddingService") to
AIE-1, and `LlmStepExecutor`'s own docstring already promised to call the LLM
"over the prompt/context (including any cited chunk carried from an upstream
`kb-retrieve`)". This module tests that promise being kept, so no other
quadrant has to change a line.
"""

from __future__ import annotations

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
from studio_engine.demo_stubs import EmptyEmbedding
from studio_engine.executors import LlmStepExecutor, build_prompt
from test_session_context_tenant_wall import default_session_context

ANKOR_ID = UUID("a0000000-0000-0000-0000-000000000001")


def _chunk(chunk_id: str, text: str) -> KbSearchResultItem:
    return KbSearchResultItem(chunk_id=chunk_id, text=text, score=0.9, tenant_id=ANKOR_ID, section_role="public")


class _RecordingLLM:
    """Captures the prompt/kwargs the executor actually sent. A double that only
    returns an answer cannot tell "the chunks reached the model" from "the model
    guessed" — the recorded prompt is the only direct evidence."""

    def __init__(self, answer: str = "ok") -> None:
        self.answer = answer
        self.prompts: list[str] = []
        self.kwargs: list[dict[str, object]] = []

    async def complete(self, prompt: str, **kwargs: object) -> str:
        self.prompts.append(prompt)
        self.kwargs.append(dict(kwargs))
        return self.answer


class _NoOpTraceWriter:
    async def write(self, event: TraceEvent) -> None:
        del event


class _FixedKbSearch:
    """Returns a fixed chunk list regardless of the query, so a prompt test is
    not also testing retrieval ranking."""

    def __init__(self, chunks: list[KbSearchResultItem]) -> None:
        self._chunks = chunks

    async def search(
        self, query: str, tenant_id: UUID, section_roles: list[str], top_k: int
    ) -> list[KbSearchResultItem]:
        del query, tenant_id, section_roles, top_k
        return list(self._chunks)


class _AltEmbedding:
    """A second, distinct `EmbeddingService` double — NOT `EmptyEmbedding`,
    NOT `StubEmbedding`. Its only purpose is to prove the Day 7 "swap
    StubEmbedding → GatewayEmbedding needs no interpreter.py change" claim: if
    `interpreter.run` type-checked or branched on the concrete embedding
    class anywhere, swapping in an unrelated double here would fail loudly
    instead of running identically."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] for _ in texts]


def test_built_prompt_carries_every_chunk_id_and_text() -> None:
    """Each retrieved chunk appears under its own bracketed `chunk_id`. The id
    must be in the prompt verbatim and in bracket form — that is the exact token
    the answer is expected to echo back and `_CITATION_RE` later extracts. A
    prompt that pastes only chunk TEXT would read fine to a human and still make
    every citation impossible."""
    chunks = [_chunk("ankor-leave-001#c1", "Báo trước 3 ngày làm việc."), _chunk("ankor-leave-001#c3", "Nghỉ ốm.")]

    prompt = build_prompt("Xin nghỉ phép báo trước bao lâu?", chunks)

    assert "[ankor-leave-001#c1]\nBáo trước 3 ngày làm việc." in prompt
    assert "[ankor-leave-001#c3]\nNghỉ ốm." in prompt


def test_multi_line_chunk_text_does_not_blur_excerpt_boundaries() -> None:
    """Real Callisto chunks are multi-line markdown — a heading, then paragraphs
    separated by blank lines (`packages/kb/docs/callisto/*.md`). Rendering them
    as `[id] text` on one line makes the start of the NEXT excerpt
    indistinguishable from a paragraph break inside the current one.

    Measured against the real corpus: `ankor-leave-001#c1` begins with the bare
    heading "Thời hạn báo trước", so anything keying on the id's own line sees a
    heading and none of the answer. Each excerpt must open with its id alone on
    a line, and excerpts must be blank-line separated."""
    a = _chunk("doc-a#c1", "Tiêu đề A\n\nNội dung A dòng 1.\nNội dung A dòng 2.")
    b = _chunk("doc-b#c1", "Tiêu đề B\n\nNội dung B.")

    prompt = build_prompt("q", [a, b])

    assert "\n[doc-a#c1]\nTiêu đề A" in prompt
    assert "\n[doc-b#c1]\nTiêu đề B" in prompt


def test_built_prompt_carries_the_question() -> None:
    """The query must reach the model verbatim; without it the chunks are
    context for a question that was never asked."""
    prompt = build_prompt("Hạn mức chi của Borea là bao nhiêu?", [_chunk("ankor-leave-001#c1", "x")])

    assert "Hạn mức chi của Borea là bao nhiêu?" in prompt


def test_built_prompt_instructs_not_to_cite_when_answer_is_absent() -> None:
    """`refused` is derived from `citations` being empty, so the model has to be
    told to withhold citations when the excerpts do not answer the question —
    otherwise SC-04 (fence blocks Borea, retrieval still returns 3 unrelated
    ankor chunks) has no way to come back as a refusal.

    This is NOT the INV-1 fence: permission is decided at retrieval
    (`umbrella-contract.md:71`), and the model is never asked to withhold data
    it was handed. It is only told not to cite what does not answer."""
    prompt = build_prompt("q", [_chunk("ankor-leave-001#c1", "x")])

    assert "KHÔNG trích dẫn" in prompt


def test_no_chunk_still_produces_a_question_bearing_prompt() -> None:
    """Empty retrieval is a valid result, not an error
    (`kb-search.v0.md` §6.1) — the executor must still send a real prompt, not
    fall back to the empty string that caused this whole defect. The absence is
    stated explicitly rather than left as a blank gap, so the model is told
    there was nothing to read instead of inferring it from whitespace."""
    prompt = build_prompt("Thang lương gồm những bậc nào?", [])

    assert "Thang lương gồm những bậc nào?" in prompt
    assert "không có đoạn trích nào" in prompt


async def test_executor_builds_prompt_when_recipe_supplies_none() -> None:
    """The published-recipe case: `llm-step` params carry no `prompt`. The
    executor must build one rather than send `""`."""
    llm = _RecordingLLM()
    node = Node(
        id="n2",
        type=NodeType.LLM_STEP,
        params={
            "query": "Xin nghỉ phép báo trước bao lâu?",
            "retrieved_chunks": [_chunk("ankor-leave-001#c1", "Báo trước 3 ngày làm việc.")],
        },
    )

    await LlmStepExecutor(llm, EmptyEmbedding()).execute(node)

    assert llm.prompts[0] != ""
    assert "[ankor-leave-001#c1]\nBáo trước 3 ngày làm việc." in llm.prompts[0]
    assert "Xin nghỉ phép báo trước bao lâu?" in llm.prompts[0]


async def test_recipe_supplied_prompt_is_used_verbatim() -> None:
    """An explicit `params["prompt"]` is a deliberate recipe-author choice and
    wins over the built one — the VCR fixtures (`tests/fixtures/llm_step/`)
    record a request prompt, and silently discarding a declared prompt would
    make those recordings a lie."""
    llm = _RecordingLLM()
    node = Node(
        id="n2",
        type=NodeType.LLM_STEP,
        params={"prompt": "prompt do recipe khai", "retrieved_chunks": [_chunk("c#c1", "x")]},
    )

    await LlmStepExecutor(llm, EmptyEmbedding()).execute(node)

    assert llm.prompts[0] == "prompt do recipe khai"


def test_build_prompt_includes_instructions_when_given() -> None:
    """Day 7: `agent_config.instructions` must reach the model. `instructions`
    is optional (default `""`) so every existing 2-arg call site above keeps
    its exact prior output — this only adds behavior when the caller opts in."""
    prompt = build_prompt("q", [], instructions="Bạn là trợ lý HR của tenant ankor.")

    assert "Bạn là trợ lý HR của tenant ankor." in prompt


def test_build_prompt_without_instructions_starts_with_the_fixed_header() -> None:
    """No regression: omitting `instructions` must still open the prompt
    directly with the fixed header — no leading instructions block, no blank
    line before it. Pinned against the literal header text (not a
    self-comparison of `build_prompt` against itself, which would pass even if
    the guard branch were removed and the header always got an empty-string
    prefix)."""
    prompt = build_prompt("q", [_chunk("ankor-leave-001#c1", "x")])

    assert prompt.startswith(
        "Bạn là trợ lý nội bộ. Chỉ dùng các đoạn trích dưới đây để trả lời, và trích dẫn chunk_id trong ngoặc vuông.\n"
    )


def test_build_prompt_with_instructions_prefixes_before_the_fixed_header() -> None:
    """The opposite pin: a non-empty `instructions` must appear BEFORE the
    fixed header, separated by a blank line — proving the two branches in
    `build_prompt` actually differ, not just that "instructions" appears
    somewhere in a long string."""
    prompt = build_prompt("q", [], instructions="Bạn là trợ lý HR của tenant ankor.")

    assert prompt.startswith(
        "Bạn là trợ lý HR của tenant ankor.\n\nBạn là trợ lý nội bộ. Chỉ dùng các đoạn trích dưới đây để trả lời, "
    )


async def test_interpreter_threads_agent_config_instructions_and_model_into_llm_step() -> None:
    """Day 7: `interpreter.run` threads `recipe.agent_config.instructions` into
    the built prompt and `recipe.agent_config.model` into the LLM call's
    `kwargs["model"]` — same inject-into-params pattern already used for
    `retrieved_chunks`/`query` (`interpreter.py`'s `LLM_STEP` branch). Neither
    the published recipe's `llm-step` node nor `_RecordingLLM` declares its
    own `prompt`/`kwargs["model"]`, so both values must come from threading,
    not a coincidence."""
    llm = _RecordingLLM()
    nodes = [
        Node(
            id="n1",
            type=NodeType.KB_RETRIEVE,
            params={"query": "Nghỉ phép năm bao nhiêu ngày?", "section_roles": ["public"], "top_k": 3},
        ),
        Node(id="n2", type=NodeType.LLM_STEP, params={}),
        Node(id="n3", type=NodeType.END, params={}),
    ]
    recipe = Recipe(
        agent_id="agent-1",
        tenant_id=ANKOR_ID,
        agent_config=AgentConfig(
            instructions="Bạn là trợ lý nội bộ tenant ankor, chỉ trả lời trong phạm vi HR.",
            model="gateway-model-x",
            tool_whitelist=[],
        ),
        dag=Dag(nodes=nodes, edges=[Edge(from_="n1", to="n2"), Edge(from_="n2", to="n3")]),
        kb_binding=KbBinding(kb_id="kb-1", scope="ankor/hr"),
        golden_set_ref="golden-1",
        scorecard_threshold=ScorecardThreshold(success=0.8, citation_accuracy=0.8),
    )

    await interpreter.run(
        recipe,
        session_context=default_session_context(),
        kb_search=_FixedKbSearch([_chunk("ankor-leave-001#c1", "12 ngày mỗi năm.")]),
        llm=llm,
        embedding=EmptyEmbedding(),
        trace_writer=_NoOpTraceWriter(),
    )

    assert "Bạn là trợ lý nội bộ tenant ankor, chỉ trả lời trong phạm vi HR." in llm.prompts[0]
    assert llm.kwargs[0]["model"] == "gateway-model-x"


async def test_swapping_the_embedding_impl_needs_no_interpreter_change() -> None:
    """Day 7 seam claim, exercised rather than assumed: `interpreter.run`
    never type-checks or branches on the concrete `EmbeddingService` impl —
    swapping `EmptyEmbedding`/`StubEmbedding` for an unrelated third double
    (`_AltEmbedding`, defined only in this test file) must run identically
    with zero changes to `interpreter.py`/`executors.py`. This is the same
    property a future `StubEmbedding` → `GatewayEmbedding` swap relies on."""
    llm = _RecordingLLM()
    nodes = [
        Node(id="n1", type=NodeType.KB_RETRIEVE, params={"query": "q", "section_roles": ["public"], "top_k": 1}),
        Node(id="n2", type=NodeType.LLM_STEP, params={}),
        Node(id="n3", type=NodeType.END, params={}),
    ]
    recipe = Recipe(
        agent_id="agent-1",
        tenant_id=ANKOR_ID,
        agent_config=AgentConfig(instructions="x", model="m", tool_whitelist=[]),
        dag=Dag(nodes=nodes, edges=[Edge(from_="n1", to="n2"), Edge(from_="n2", to="n3")]),
        kb_binding=KbBinding(kb_id="kb-1", scope="ankor/hr"),
        golden_set_ref="golden-1",
        scorecard_threshold=ScorecardThreshold(success=0.8, citation_accuracy=0.8),
    )

    result = await interpreter.run(
        recipe,
        session_context=default_session_context(),
        kb_search=_FixedKbSearch([_chunk("ankor-leave-001#c1", "x")]),
        llm=llm,
        embedding=_AltEmbedding(),
        trace_writer=_NoOpTraceWriter(),
    )

    assert result.final_state["n3"] == {"terminated": True}


async def test_interpreter_threads_the_walk_upstream_query_into_llm_step() -> None:
    """The query lives on the `kb-retrieve` node; `llm-step` has no query of its
    own. `interpreter.run` threads it the same way it threads
    `retrieved_chunks` — from the `kb-retrieve` the WALK passed through, so a
    recipe that declares nodes out of execution order still asks the right
    question (same reason the chunk threading stopped using a by-type lookup)."""
    llm = _RecordingLLM()
    nodes = [
        Node(
            id="n1",
            type=NodeType.KB_RETRIEVE,
            params={"query": "Trưởng nhóm được duyệt chi tối đa bao nhiêu?", "section_roles": ["finance"], "top_k": 3},
        ),
        Node(id="n2", type=NodeType.LLM_STEP, params={"temperature": 0.0}),
        Node(id="n3", type=NodeType.END, params={}),
    ]
    recipe = Recipe(
        agent_id="agent-1",
        tenant_id=ANKOR_ID,
        agent_config=AgentConfig(instructions="x", model="m", tool_whitelist=[]),
        dag=Dag(nodes=nodes, edges=[Edge(from_="n1", to="n2"), Edge(from_="n2", to="n3")]),
        kb_binding=KbBinding(kb_id="kb-1", scope="ankor/finance"),
        golden_set_ref="golden-1",
        scorecard_threshold=ScorecardThreshold(success=0.8, citation_accuracy=0.8),
    )

    await interpreter.run(
        recipe,
        session_context=default_session_context(),
        kb_search=_FixedKbSearch([_chunk("ankor-expense-001#c2", "Hạn mức 20 triệu đồng.")]),
        llm=llm,
        embedding=EmptyEmbedding(),
        trace_writer=_NoOpTraceWriter(),
    )

    assert "Trưởng nhóm được duyệt chi tối đa bao nhiêu?" in llm.prompts[0]
    assert "[ankor-expense-001#c2]\nHạn mức 20 triệu đồng." in llm.prompts[0]
