"""Phase 1 (plan `260811-1121-d17-aie1-section-roles-server-resolve`, issue
kit#111, AIE-1) — T6 label-spoof, the `section_roles` sibling of Day 8's
`test_session_context_tenant_wall.py` money-shot.

`interpreter.run()` must inject `section_roles` server-resolved from
`session_context.roles` into the `kb-retrieve` node's params, mirroring the
`tenant_id` override already proven at `interpreter.py:291` — a
client-declared `node.params["section_roles"]` (built from the recipe's
`kb_binding.scope`, `packages/workbench/src/studio_workbench/builder.py`)
must never reach `KbSearch.search(...)`. Before this phase, `executors.py`
read `section_roles` as-given from `node.params` (`executors.py:139`) and
`interpreter.py` overwrote nothing — a client that declares a wider
`section_roles` than its actual session roles gets exactly that wider scope,
the T6 label-spoof `KbRetrieveExecutor`'s own docstring already claimed was
closed but was not.

QĐ-3 (plan.md): this file asserts at the INPUT of `kb.search` (the executor
boundary), inside `interpreter.run()`'s real dispatch — it is the AIE-1 half
of T6, distinct from (not a replacement for) DE's
`packages/kb/tests/test_leak.py::test_t6_label_spoof` (kb-lane invariant,
deliberately still `xfail` at D17).

Session-context double: reuses `_FrozenSessionContext`/
`default_session_context()`/`ANKOR_ID` from `test_session_context_tenant_wall`
(that module's own docstring: "the ONE shared session-context builder") —
this file does NOT define a second session dataclass (DRY), and does not
modify that file (only imports from it, per plan.md constraints).
"""

from __future__ import annotations

from uuid import UUID

import pytest
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
from studio_engine.executors import KbRetrieveExecutor
from test_session_context_tenant_wall import ANKOR_ID, _FrozenSessionContext, default_session_context


class _RolesCapturingKbSearch:
    """Records `section_roles` that `KbRetrieveExecutor` ACTUALLY calls
    `kb.search` with — proves what reached the retrieval boundary, not just
    what the recipe declared. Same shape as
    `_TenantCapturingKbSearch` (test_session_context_tenant_wall.py:71-88)."""

    def __init__(self) -> None:
        self.seen_section_roles: list[str] | None = None

    async def search(
        self,
        query: str,
        tenant_id: UUID,
        section_roles: list[str],
        top_k: int,
    ) -> list[KbSearchResultItem]:
        del query, tenant_id, top_k
        self.seen_section_roles = section_roles
        return []


class _NoOpTraceWriter:
    async def write(self, event: TraceEvent) -> None:
        del event


def _kb_then_end_recipe(*, node_params: dict[str, object]) -> Recipe:
    """Minimal 2-node `kb-retrieve -> end` recipe — same shape as
    `test_session_context_tenant_wall.py::_kb_then_end_recipe`, but the
    variable half is the `kb-retrieve` node's own `params` (this file's
    money-shot needs to seed `section_roles` there, not `tenant_id`)."""
    nodes = [
        Node(id="n_kb", type=NodeType.KB_RETRIEVE, params=node_params),
        Node(id="n_end", type=NodeType.END, params={}),
    ]
    return Recipe(
        agent_id="agent-1",
        tenant_id=ANKOR_ID,
        agent_config=AgentConfig(instructions="x", model="m", tool_whitelist=[]),
        dag=Dag(nodes=nodes, edges=[Edge(from_="n_kb", to="n_end")]),
        kb_binding=KbBinding(kb_id="kb-1", scope="ankor/public"),
        golden_set_ref="golden-1",
        scorecard_threshold=ScorecardThreshold(success=0.8, citation_accuracy=0.8),
    )


async def test_kb_search_receives_session_roles_not_recipe_roles() -> None:
    """T6-full, money-shot. Recipe node declares `section_roles=["finance"]`
    (client-authored), session carries `roles=["public"]` (server-resolved).
    `kb.search` must see the SESSION's roles, never the recipe's — asserted
    both ways so a fixed-return stub can't false-pass the negative half."""
    kb_search = _RolesCapturingKbSearch()
    recipe = _kb_then_end_recipe(node_params={"section_roles": ["finance"]})

    await interpreter.run(
        recipe,
        session_context=_FrozenSessionContext(tenant_id=ANKOR_ID, user="test-user", roles=["public"]),
        kb_search=kb_search,
        llm=None,  # type: ignore[arg-type]
        embedding=EmptyEmbedding(),
        trace_writer=_NoOpTraceWriter(),
    )

    assert kb_search.seen_section_roles == ["public"]
    assert "finance" not in (kb_search.seen_section_roles or [])


async def test_node_params_section_roles_is_overwritten_not_merged() -> None:
    """Session `roles=["public", "hr"]`, node declares
    `section_roles=["finance", "engineering"]` — the override must OVERWRITE,
    not merge/union: no recipe-declared role may survive into the value that
    reaches `kb.search`. Pins the merge-order hazard `[*session, *node]` or
    `{**session_first, **node_params}` would silently reopen (same class of
    bug `test_session_context_tenant_wall.py:166-190` pins for `tenant_id`)."""
    kb_search = _RolesCapturingKbSearch()
    recipe = _kb_then_end_recipe(node_params={"section_roles": ["finance", "engineering"]})

    await interpreter.run(
        recipe,
        session_context=_FrozenSessionContext(tenant_id=ANKOR_ID, user="test-user", roles=["public", "hr"]),
        kb_search=kb_search,
        llm=None,  # type: ignore[arg-type]
        embedding=EmptyEmbedding(),
        trace_writer=_NoOpTraceWriter(),
    )

    seen = set(kb_search.seen_section_roles or [])
    assert seen == {"public", "hr"}
    assert seen & {"finance", "engineering"} == set()


async def test_missing_section_roles_param_fails_closed_to_empty_list() -> None:
    """Executor-direct call (bypassing `interpreter.run()`, simulating a
    defense-in-depth boundary probe): `node.params` carries no
    `section_roles` key at all. This PINS existing `executors.py:139`
    behavior (`node.params.get("section_roles", [])`) — it is already GREEN
    before Phase 1's `interpreter.py` change, not a red->green case. Deny-all
    is the correct fail-closed default: no key present must never be read as
    "no filter" (wildcard) or `None`."""
    kb_search = _RolesCapturingKbSearch()
    node = Node(id="n_kb", type=NodeType.KB_RETRIEVE, params={"tenant_id": ANKOR_ID})

    await KbRetrieveExecutor(kb_search).execute(node)

    assert kb_search.seen_section_roles == []
    assert kb_search.seen_section_roles is not None
    assert "*" not in (kb_search.seen_section_roles or [])


async def test_non_list_section_roles_at_executor_boundary_is_not_coerced() -> None:
    """Executor-direct call: `section_roles="public"` (a bare str, not a
    list) — PINS existing `executors.py:153`
    (`isinstance(raw_roles, list)`) behavior, already green pre-Phase-1.

    Scope of this guarantee (two things, or the test lies about what it
    covers): (a) this is a pin of already-existing executor behavior, not a
    new red->green case; (b) after Phase 1, this code path is UNREACHABLE
    from `interpreter.run()`'s real dispatch — the walk always overwrites
    `section_roles` before the executor ever reads `node.params`, so this
    only guarantees the direct-call (defense-in-depth) case. The real-walk
    guarantee for malformed input is `test_malformed_session_roles_fail_
    closed_through_real_run` below."""
    kb_search = _RolesCapturingKbSearch()
    node = Node(id="n_kb", type=NodeType.KB_RETRIEVE, params={"section_roles": "public", "tenant_id": ANKOR_ID})

    await KbRetrieveExecutor(kb_search).execute(node)

    assert kb_search.seen_section_roles == []
    assert kb_search.seen_section_roles != ["public"]
    assert "public" not in (kb_search.seen_section_roles or [])


@pytest.mark.parametrize("bad_roles", [None, "public"])
async def test_malformed_session_roles_fail_closed_through_real_run(bad_roles: object) -> None:
    """MANDATORY — goes through the real `interpreter.run()` dispatch (not a
    direct executor call). A `SessionContext` double that mis-declares
    `.roles` as `None` or a bare `str` (a type violation only a bad/buggy
    double can construct — `# type: ignore[arg-type]` right here, at the
    exact spot the lie is declared, never in `interpreter.py`) must still
    reach `kb.search` with `[]` — never `list(None)` (raises `TypeError`
    mid-walk, escapes uncaught) and never `list("public")` (`['p', 'u', 'b',
    'l', 'i', 'c']` — 6 garbage roles, a WIDENING of scope, not deny-all)."""
    kb_search = _RolesCapturingKbSearch()
    recipe = _kb_then_end_recipe(node_params={"section_roles": ["finance"]})
    bad_session = _FrozenSessionContext(tenant_id=ANKOR_ID, user="test-user", roles=bad_roles)  # type: ignore[arg-type]

    result = await interpreter.run(
        recipe,
        session_context=bad_session,
        kb_search=kb_search,
        llm=None,  # type: ignore[arg-type]
        embedding=EmptyEmbedding(),
        trace_writer=_NoOpTraceWriter(),
    )

    assert len(result.events) == 2
    assert kb_search.seen_section_roles == []
    assert kb_search.seen_section_roles != list("public")


def test_default_session_context_still_importable() -> None:
    """Cheap smoke check that the shared builder import (this file's only
    touch of `test_session_context_tenant_wall`) still resolves — a stale
    import here would otherwise only surface as a confusing collection error
    in an unrelated test."""
    ctx = default_session_context()
    assert ctx.tenant_id == ANKOR_ID
