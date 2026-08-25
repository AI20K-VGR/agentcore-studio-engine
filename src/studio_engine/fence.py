"""Shared tenant/section_roles fence for the `kb-retrieve` node params (Day 8
INV-1 / Day 17 T6 label-spoof) — extracted from `interpreter.py::run()`'s
previously-inline override block (engine#33 phase 2). Both `interpreter.run()`
and `agent_loop.run_agent_loop()` (phase 3) call this ONE function; issue #33
itself flags 2 independent fence copies drifting apart as the exact failure
mode this refactor closes off.

Tag-vs-isolation (see `studio_engine.session` module docstring for the full
argument): a `tenant_id`/`section_roles` value on a client-authored node's
`params` is a CLAIM, not a guarantee — anything the client (or, for the agent
loop, the model itself via a `TOOL_CALL:` it emits) can set is untrusted. The
only real fence is overriding those 2 keys with the SESSION's server-resolved
values, unconditionally, after any client-declared value has already been
spread into the dict — never merely supplementing what is missing.

`tenant_id` — always exactly `session_context.tenant_id`, no type check here.
This function does not validate the shape of `session_context.tenant_id`; a
malformed value still fails closed one layer down, inside
`KbRetrieveExecutor.execute` (`executors.py:170-178`, raises `PermissionError`
if the post-override value is not a real `UUID`). Duplicating that check here
would both violate DRY and — for `interpreter.run()` specifically — change its
behavior (K2: `run()`'s behavior must stay bit-for-bit identical after this
refactor), so it deliberately stays a single layer of defense, not two.

`section_roles` — coerced EXACTLY like the inline block it replaces
(`interpreter.py:361-362` pre-refactor): `[str(r) for r in raw] if
isinstance(raw, list) else []`. No sort, no dedupe, no injected `"public"`, no
`SECTION_VOCAB` validation (QĐ-5, `.importlinter` forbids `studio_engine`
importing `studio_kb`, which owns that vocabulary anyway) — values pass through
UNCHANGED after type coercion.

Why `raw` not being a `list` becomes `[]` and NEVER raises (probed for real,
not assumed — same evidence `interpreter.py:344-355`'s pre-refactor comment
recorded): `list("public")` -> `['p','u','b','l','i','c']`, 6 garbage roles
that WIDEN scope rather than deny it; `list(None)` raises a bare `TypeError`
mid-walk with nothing there to catch it. Both are worse than a clean
fail-closed `[]`. `[]` is already the correct deny-all value at retrieval
(`static_search.py`: `allowed = set(section_roles)`, no match -> 0 chunks), so
there is no `PermissionError`-shaped raise branch for roles the way there is
for `tenant_id` above — QĐ-7 (plan `260811-1121-d17`).

The real `resolve_session` (SWE, `tenant_wall.py:184-195`) already normalizes
in production; a malformed `.system_roles` only reaches this fallback through a
test double that mis-declares it, or (new in the agent-loop path, phase 3) a
model-declared `section_roles` on a `TOOL_CALL:` payload — the same
override-after-spread rule closes that off identically to the DAG path.
"""

from __future__ import annotations

from collections.abc import Mapping

from studio_engine.session import SessionContext


def fenced_kb_params(params: Mapping[str, object], session_context: SessionContext) -> dict[str, object]:
    """Return a NEW dict = `{**params, "tenant_id": ..., "section_roles":
    ...}`, with the 2 session-derived keys always winning over whatever
    `params` already carried (spread first, override after — never merged).
    `params` itself is never mutated."""
    raw_roles = session_context.system_roles
    section_roles = [str(role) for role in raw_roles] if isinstance(raw_roles, list) else []
    return {
        **params,
        "tenant_id": session_context.tenant_id,
        "section_roles": section_roles,
    }
