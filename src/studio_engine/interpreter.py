"""Interpreter loop (spec AIE-1, R-SPEC A2) — walks `recipe.dag`, dispatches
each node to its registered executor (`registry.get_executor_class`), and
emits a `TraceEvent` per node via the injected `TraceWriter`. Body is
`NotImplementedError`: this phase pins the CONTRACT (signature + docstring)
only, never the walk/dispatch/emit logic — that is AIE-1's own OJT
deliverable, same anti-pattern the phase's risk table calls out for the
executors ("Executor làm xanh hộ") applies here too.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from studio_contracts import Recipe, TraceEvent, TraceWriter


@dataclass(frozen=True)
class RunResult:
    """Placeholder return shape for `run()`. AIE-1 fills in the real fields
    (e.g. the terminal `end` node's output, aggregate cost) alongside the
    real interpreter body. This is `studio_engine`'s own type, NOT one of the
    4 seam contracts frozen at P2 (R-SPEC A1) — it never needs a mini-RFC to
    change shape.
    """

    run_id: str
    events: list[TraceEvent] = field(default_factory=list)


async def run(recipe: Recipe, *, trace_writer: TraceWriter) -> RunResult:
    """Walk `recipe.dag` from its entry node(s), dispatch each node to its
    executor by `node.type` (`registry.get_executor_class`), execute it, and
    emit one `TraceEvent` per node via `trace_writer.write(...)` before
    advancing along `recipe.dag.edges` (branching through `condition` nodes
    per `edges[].when`) until an `end` node terminates the run and its
    `RunResult` is returned.

    Body intentionally `NotImplementedError` (spec AIE-1, R-SPEC A2) — the
    walk/dispatch/emit logic is AIE-1's own deliverable to fill in, not this
    phase's.
    """
    raise NotImplementedError("spec AIE-1: interpreter run() body — see R-SPEC A2")
