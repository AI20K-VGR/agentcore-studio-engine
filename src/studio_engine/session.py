"""`SessionContext` — the server-resolved identity `interpreter.run()` requires
(spec AIE-1, Day 8, INV-1 Tenant-Wall).

Shape = a `typing.Protocol` declared HERE, in `studio_engine`, not reused from
`studio_workbench.tenant_wall.ResolvedContext` (SWE, PR #6): `.importlinter`'s
layers-contract forbids `studio_engine` importing `studio_workbench` (both are
sibling quadrants under `studio_app`). Structural typing means SWE's
`ResolvedContext` already satisfies this Protocol without any adapter or cross
import — the composition root (`apps/studio`, the one layer allowed to import
both quadrants) can pass it straight through.

Tag-vs-isolation: a `tenant_id` field on a request is a TAG, not a fence by
itself — anything that merely *carries* the value (a request body, a recipe
document, an HTTP header a client can set) is a claim, not a guarantee. What
makes tenant scoping real ISOLATION is that a value's only path onto the wire
is one the client cannot influence: `session_context` is resolved
server-side (the composition root builds it from the authenticated session,
never from `recipe.tenant_id`, which is client-supplied data). `interpreter.py`
consuming `session_context.tenant_id` instead of `recipe.tenant_id` is exactly
this fence — see `executors.py:100-106` (`KbRetrieveExecutor`, the
fence-EXECUTOR layer) for the sibling half of the same argument at the
retrieval boundary, and `trace.py:30` (`TraceEvent.tenant_id`, `NOT NULL,
INV-1`) for why the field this Protocol supplies is load-bearing all the way
into the audit trail.

"Nhờ LLM đừng nói" (asking the model to withhold what it was already handed)
is the fake-fence anti-pattern this guards against: telling an LLM not to
repeat data is not isolation, because nothing stops a differently-phrased
prompt from extracting it anyway. The only real fence is never handing the
model (or the executor, or the trace) data outside its authorized tenant in
the first place — which is why `session_context` is a **mandatory**
keyword-only parameter on `run()` (no default), not an optional override a
caller could omit and fall back to trusting the recipe.

3 members are declared even though only `tenant_id` has a consumer today:
`user`/`roles` exist to match `ResolvedContext`'s shape (so the structural
match holds now, not just once a consumer lands) and for Sprint 3's
`section_role` filter — same precedent as Day 7 threading `agent_config.model`
before any executor read it.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID


@runtime_checkable
class SessionContext(Protocol):
    """Server-resolved caller identity. 3 read-only properties (not plain
    attributes) so a `@dataclass(frozen=True)` — whose fields cannot be
    reassigned — structurally satisfies this Protocol under `mypy --strict`;
    a Protocol declared with plain mutable attributes would reject a frozen
    dataclass as a variance mismatch (see phase-1 Probe)."""

    @property
    def tenant_id(self) -> UUID: ...

    @property
    def user(self) -> str: ...

    @property
    def roles(self) -> list[str]: ...
