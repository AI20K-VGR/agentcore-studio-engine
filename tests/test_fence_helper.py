"""Phase 2 (plan `260823-1249-engine33-agent-loop`, engine#33) — tests-before for
`studio_engine.fence.fenced_kb_params`, the shared helper extracted from
`interpreter.py`'s previously-inline tenant/section_roles override
(`interpreter.py:324-371` pre-refactor). Both `interpreter.run()` and
`agent_loop.run_agent_loop()` (phase 3) call this ONE helper — 2 fence copies
drifting apart is exactly the tenant-leak risk issue #33 calls out.

Every behavior here is a 1:1 copy of the inline block being replaced — see
`fenced_kb_params`'s own docstring for the probed evidence (`list("public")`,
`list(None)`) this pins.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from studio_engine.fence import fenced_kb_params
from studio_engine.session import SessionContext

ANKOR_ID = UUID("a0000000-0000-0000-0000-000000000001")
BOREA_ID = UUID("b0000000-0000-0000-0000-000000000001")


@dataclass(frozen=True, slots=True)
class _FrozenSessionContext:
    tenant_id: UUID
    user: str
    roles: list[str]


# Distinct from `None` — several tests below pass `roles=None` DELIBERATELY
# (F5, a malformed value fenced_kb_params must coerce, not the default).
_NO_ROLES_GIVEN = object()


def _session(tenant_id: UUID = ANKOR_ID, roles: object = _NO_ROLES_GIVEN) -> SessionContext:
    """`roles` is deliberately typed `object`, not `list[str]` — several tests
    below pass a malformed value (a bare string, `None`, a list of non-str
    items) to exercise `fenced_kb_params`'s coercion fallback, so the
    constructed double must accept whatever shape a test hands it."""
    actual_roles: object = [] if roles is _NO_ROLES_GIVEN else roles
    return _FrozenSessionContext(tenant_id=tenant_id, user="test-user", roles=actual_roles)  # type: ignore[arg-type]


def test_session_tenant_overrides_client_declared() -> None:
    result = fenced_kb_params({"tenant_id": BOREA_ID}, _session(tenant_id=ANKOR_ID))
    assert result["tenant_id"] == ANKOR_ID
    assert result["tenant_id"] != BOREA_ID


def test_session_roles_override_client_declared() -> None:
    result = fenced_kb_params({"section_roles": ["finance"]}, _session(roles=["public"]))
    assert result["section_roles"] == ["public"]


def test_other_params_survive() -> None:
    result = fenced_kb_params({"query": "q", "top_k": 5}, _session())
    assert result["query"] == "q"
    assert result["top_k"] == 5


def test_malformed_roles_str_becomes_deny_all() -> None:
    result = fenced_kb_params({}, _session(roles="public"))
    assert result["section_roles"] == []


def test_malformed_roles_none_becomes_deny_all() -> None:
    result = fenced_kb_params({}, _session(roles=None))
    assert result["section_roles"] == []


def test_non_str_roles_coerced() -> None:
    result = fenced_kb_params({}, _session(roles=[1, None]))
    assert result["section_roles"] == ["1", "None"]


def test_roles_order_and_duplicates_preserved() -> None:
    result = fenced_kb_params({}, _session(roles=["b", "a", "b"]))
    assert result["section_roles"] == ["b", "a", "b"]


def test_input_params_not_mutated() -> None:
    params = {"tenant_id": BOREA_ID, "section_roles": ["x"]}
    original = dict(params)
    fenced_kb_params(params, _session())
    assert params == original


def test_returns_new_dict() -> None:
    params = {"query": "q"}
    result = fenced_kb_params(params, _session())
    assert result is not params


def test_no_tenant_type_check_here() -> None:
    """Helper KHÔNG raise cho tenant_id sai kiểu — lớp fail-closed đó thuộc
    `KbRetrieveExecutor` (executors.py:170-178), không bị nhân bản ở đây."""

    @dataclass(frozen=True, slots=True)
    class _BadSession:
        tenant_id: object
        user: str
        roles: list[str]

    bad_session = _BadSession(tenant_id="not-a-uuid", user="u", roles=[])
    result = fenced_kb_params({}, bad_session)  # type: ignore[arg-type]
    assert result["tenant_id"] == "not-a-uuid"
