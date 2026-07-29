"""Money-shot test for Day 8 (AIE-1, issue #37, INV-1 Tenant-Wall):
`interpreter.run()` must take tenant identity from `session_context`
(server-resolved), NEVER from `recipe.tenant_id` (client-declared data) —
even when the two disagree. DoD, verbatim: "Client gửi `tenant=borea` khi
session là `ankor` → `kb.search` **chỉ** trả ankor". The request still runs
(no raise); it is scoped, not refused.

`default_session_context()` (this module) is the ONE shared session-context
builder Phase 1's other 7 updated test files import — every one of them
already builds recipes with `tenant_id=ANKOR_ID`, so passing a same-tenant
session leaves their existing (pre-Day-8) assertions unchanged; only the
tests in THIS file exercise the mismatch.
"""

from __future__ import annotations

from dataclasses import dataclass
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
from studio_engine.session import SessionContext

# Same canonical UUIDs used across quadrants — packages/kb/tests/test_trace_reader.py:40,
# packages/workbench/tests/test_wiring_d6.py:20 (BOREA_ID); __main__.py:35 and every
# other Phase-1 test file (ANKOR_ID).
ANKOR_ID = UUID("a0000000-0000-0000-0000-000000000001")
BOREA_ID = UUID("b0000000-0000-0000-0000-000000000001")


@dataclass(frozen=True, slots=True)
class _FrozenSessionContext:
    """Probe fixture (phase-1 mypy Probe) doubling as the real test double:
    a frozen dataclass with plain data members. `SessionContext` declares its
    3 members as `@property` specifically so THIS shape satisfies it under
    `mypy --strict` without an adapter."""

    tenant_id: UUID
    user: str
    roles: list[str]


def default_session_context(tenant_id: UUID = ANKOR_ID) -> SessionContext:
    """Shared builder imported by the other 6 Phase-1 test files (plus
    `test_cli_demo.py`) so `run()`'s new mandatory `session_context` doesn't
    get 7 copy-pasted definitions."""
    return _FrozenSessionContext(tenant_id=tenant_id, user="test-user", roles=[])


def _accepts_session_context(ctx: SessionContext) -> UUID:
    """mypy-strict Probe helper — a function typed to accept `SessionContext`
    and read `.tenant_id`, called with a `_FrozenSessionContext` instance.
    Confirms the frozen-dataclass-vs-Protocol variance mismatch risk (plan.md
    risk M) does not materialize with a read-only `@property` Protocol."""
    return ctx.tenant_id


class _TenantCapturingKbSearch:
    """Records the `tenant_id` `KbRetrieveExecutor` actually calls
    `KbSearch.search(...)` with — proves what reached the retrieval boundary,
    not just what the recipe declared."""

    def __init__(self) -> None:
        self.seen_tenant_id: UUID | None = None

    async def search(
        self,
        query: str,
        tenant_id: UUID,
        section_roles: list[str],
        top_k: int,
    ) -> list[KbSearchResultItem]:
        del query, section_roles, top_k
        self.seen_tenant_id = tenant_id
        return []


class _NoOpTraceWriter:
    async def write(self, event: TraceEvent) -> None:
        del event


def _kb_then_end_recipe(*, recipe_tenant_id: UUID) -> Recipe:
    """Minimal 2-node `kb-retrieve -> end` recipe. `recipe_tenant_id` is the
    CLIENT-declared value on the recipe document — the money-shot tests set
    this to `BOREA_ID` while the session carries `ANKOR_ID`, to prove the
    mismatch is resolved in the session's favor everywhere `run()` reads
    tenant identity."""
    nodes = [
        Node(id="n_kb", type=NodeType.KB_RETRIEVE, params={}),
        Node(id="n_end", type=NodeType.END, params={}),
    ]
    return Recipe(
        agent_id="agent-1",
        tenant_id=recipe_tenant_id,
        agent_config=AgentConfig(instructions="x", model="m", tool_whitelist=[]),
        dag=Dag(nodes=nodes, edges=[Edge(from_="n_kb", to="n_end")]),
        kb_binding=KbBinding(kb_id="kb-1", scope="ankor/public"),
        golden_set_ref="golden-1",
        scorecard_threshold=ScorecardThreshold(success=0.8, citation_accuracy=0.8),
    )


async def test_frozen_dataclass_satisfies_session_context_protocol() -> None:
    ctx = _FrozenSessionContext(tenant_id=ANKOR_ID, user="u", roles=["viewer"])

    assert isinstance(ctx, SessionContext)
    assert _accepts_session_context(ctx) == ANKOR_ID


async def test_run_requires_session_context() -> None:
    """`run()` has no default for `session_context` — omitting it is a
    `TypeError` at call time, not a silent None/sentinel fallback."""
    recipe = _kb_then_end_recipe(recipe_tenant_id=ANKOR_ID)
    try:
        await interpreter.run(  # type: ignore[call-arg]
            recipe,
            kb_search=_TenantCapturingKbSearch(),
            llm=None,  # type: ignore[arg-type]
            embedding=EmptyEmbedding(),
            trace_writer=_NoOpTraceWriter(),
        )
    except TypeError:
        return
    raise AssertionError("run() must raise TypeError when session_context is omitted")


async def test_kb_search_receives_session_tenant_not_recipe_tenant() -> None:
    """Money-shot: recipe declares `tenant_id=BOREA_ID`, session carries
    `ANKOR_ID`. `KbSearch.search(...)` must be called with `ANKOR_ID` —
    asserted BOTH ways (equals ANKOR, not equals BOREA) so a fixed-return
    stub can't false-pass the negative half."""
    kb_search = _TenantCapturingKbSearch()
    recipe = _kb_then_end_recipe(recipe_tenant_id=BOREA_ID)

    await interpreter.run(
        recipe,
        session_context=default_session_context(tenant_id=ANKOR_ID),
        kb_search=kb_search,
        llm=None,  # type: ignore[arg-type]
        embedding=EmptyEmbedding(),
        trace_writer=_NoOpTraceWriter(),
    )

    assert kb_search.seen_tenant_id == ANKOR_ID
    assert kb_search.seen_tenant_id != BOREA_ID


async def test_trace_event_tenant_id_comes_from_session() -> None:
    """Every `TraceEvent` in `result.events` carries `tenant_id == ANKOR_ID`
    (session) — none carry `BOREA_ID` (recipe), across ALL dispatched nodes,
    not just the first."""
    recipe = _kb_then_end_recipe(recipe_tenant_id=BOREA_ID)

    result = await interpreter.run(
        recipe,
        session_context=default_session_context(tenant_id=ANKOR_ID),
        kb_search=_TenantCapturingKbSearch(),
        llm=None,  # type: ignore[arg-type]
        embedding=EmptyEmbedding(),
        trace_writer=_NoOpTraceWriter(),
    )

    assert len(result.events) == 2
    for event in result.events:
        assert event.tenant_id == ANKOR_ID
        assert event.tenant_id != BOREA_ID
