"""LangGraph orchestration of the six-agent pipeline.

If LangGraph is importable we build a real async ``StateGraph`` that runs
Extraction first, then Forensics, Policy, History, and Ring detection concurrently in
parallel, and finishes with Verdict Fusion.

Channels that receive updates from multiple concurrent nodes in the same step
(e.g. ``agent_status`` and ``errors``) use ``typing.Annotated`` with dictionary
reducers to prevent INVALID_CONCURRENT_GRAPH_UPDATE errors.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any, AsyncIterator, Awaitable, Callable, Optional
from typing_extensions import TypedDict

from app.agents import (
    ExtractionAgent,
    ForensicsAgent,
    HistoryAgent,
    PolicyAgent,
    RingDetectionAgent,
    VerdictAgent,
)
from app.schemas.models import AgentName, AgentStatus, PipelineState

logger = logging.getLogger(__name__)

try:
    from langgraph.graph import END, StateGraph  # type: ignore

    _HAS_LANGGRAPH = True
except Exception:  # pragma: no cover - optional
    _HAS_LANGGRAPH = False


AgentRunner = Callable[[PipelineState], Awaitable[PipelineState]]


def _merge_dict(a: dict | None, b: dict | None) -> dict:
    """Reducer for dictionary state channels updated concurrently."""
    res = dict(a) if a else {}
    if b:
        res.update(b)
    return res


class PipelineGraphState(TypedDict, total=False):
    document_id: str
    filename: str
    image_path: str
    extraction: Optional[dict]
    forensics: Optional[dict]
    policy: Optional[dict]
    history: Optional[dict]
    ring: Optional[dict]
    verdict: Optional[dict]
    agent_status: Annotated[dict[str, Any], _merge_dict]
    errors: Annotated[dict[str, str], _merge_dict]


# Pipeline structure:
# Extraction must run first to parse the document record.
# Forensics, Policy, History, and Ring run concurrently in parallel.
# Verdict Fusion runs last to synthesize all multi-modal signals.
_PARALLEL_AGENTS: list[AgentName] = [
    AgentName.FORENSICS,
    AgentName.POLICY,
    AgentName.HISTORY,
    AgentName.RING,
]

_PIPELINE_ORDER: list[AgentName] = [
    AgentName.EXTRACTION,
    *_PARALLEL_AGENTS,
    AgentName.VERDICT,
]


def _build_agents() -> dict[AgentName, Any]:
    return {
        AgentName.EXTRACTION: ExtractionAgent(),
        AgentName.FORENSICS: ForensicsAgent(),
        AgentName.POLICY: PolicyAgent(),
        AgentName.HISTORY: HistoryAgent(),
        AgentName.RING: RingDetectionAgent(),
        AgentName.VERDICT: VerdictAgent(),
    }


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


async def run_pipeline(state: PipelineState) -> AsyncIterator[dict[str, Any]]:
    """Yield SSE events while running the six-agent parallel pipeline."""
    agents = _build_agents()
    if _HAS_LANGGRAPH:
        event_queue: asyncio.Queue = asyncio.Queue()
        cumulative_state = state.model_copy(deep=True)
        graph = _build_state_graph(agents, event_queue, cumulative_state)
        compiled = graph.compile()
        async for event in _run_with_langgraph(compiled, state, event_queue, cumulative_state):
            yield event
        return

    async for event in _run_parallel(state, agents):
        yield event


# --------------------------------------------------------------------------- #
# LangGraph state machine with parallel fan-out
# --------------------------------------------------------------------------- #


def _build_state_graph(
    agents: dict[AgentName, Any],
    event_queue: asyncio.Queue,
    cumulative_state: PipelineState,
):
    """Build a parallel LangGraph DAG: extraction -> [parallel 4] -> verdict."""

    def make_node(name: AgentName):
        async def _node(state_dict: dict) -> dict:
            await event_queue.put({"event": "agent_start", "agent": name.value})
            ps = PipelineState.model_validate(state_dict)
            ps.agent_status[name.value] = AgentStatus.RUNNING
            try:
                await agents[name].run(ps)
            except Exception as exc:
                logger.exception("agent %s failed", name.value)
                ps.errors[name.value] = str(exc)
                ps.agent_status[name.value] = AgentStatus.ERROR
                await event_queue.put({
                    "event": "error",
                    "agent": name.value,
                    "detail": str(exc),
                })

            field_val = getattr(ps, name.value, None)
            raw_st = ps.agent_status.get(name.value, AgentStatus.PASSED)
            status_str = raw_st.value if isinstance(raw_st, AgentStatus) else str(raw_st)

            updates: dict[str, Any] = {
                name.value: field_val.model_dump(mode="json") if hasattr(field_val, "model_dump") else field_val,
                "agent_status": {name.value: status_str},
            }
            if name.value in ps.errors:
                updates["errors"] = {name.value: ps.errors[name.value]}

            # Mirror updates into cumulative_state for live SSE snapshots
            if field_val is not None:
                setattr(cumulative_state, name.value, field_val)
            cumulative_state.agent_status[name.value] = raw_st
            if name.value in ps.errors:
                cumulative_state.errors[name.value] = ps.errors[name.value]

            await event_queue.put({
                "event": "agent_done",
                "agent": name.value,
                "result": cumulative_state.model_dump(mode="json"),
            })
            return updates

        _node.__name__ = f"node_{name.value}"
        return _node

    graph = StateGraph(PipelineGraphState)
    for name in _PIPELINE_ORDER:
        graph.add_node(f"node_{name.value}", make_node(name))

    graph.set_entry_point(f"node_{AgentName.EXTRACTION.value}")

    # Fan out to parallel agents
    for parallel_name in _PARALLEL_AGENTS:
        graph.add_edge(f"node_{AgentName.EXTRACTION.value}", f"node_{parallel_name.value}")
        graph.add_edge(f"node_{parallel_name.value}", f"node_{AgentName.VERDICT.value}")

    graph.add_edge(f"node_{AgentName.VERDICT.value}", END)
    return graph


async def _run_with_langgraph(
    compiled,
    state: PipelineState,
    event_queue: asyncio.Queue,
    cumulative_state: PipelineState,
) -> AsyncIterator[dict[str, Any]]:
    """Execute the compiled LangGraph DAG while streaming SSE events in real-time."""
    init_state = {
        "document_id": state.document_id,
        "filename": state.filename,
        "image_path": state.image_path,
        "agent_status": {k: (v.value if isinstance(v, AgentStatus) else str(v)) for k, v in state.agent_status.items()},
        "errors": dict(state.errors),
    }

    graph_task = asyncio.create_task(compiled.ainvoke(init_state))

    try:
        while not graph_task.done() or not event_queue.empty():
            try:
                event = await asyncio.wait_for(event_queue.get(), timeout=0.1)
                yield event
            except asyncio.TimeoutError:
                pass

        final_res = await graph_task
        # Reconcile any final updates into cumulative_state
        for name in _PIPELINE_ORDER:
            val = final_res.get(name.value)
            if val is not None and getattr(cumulative_state, name.value, None) is None:
                setattr(cumulative_state, name.value, val)

        yield {"event": "pipeline_done", "state": cumulative_state.model_dump(mode="json")}
    except Exception as exc:
        logger.exception("LangGraph run failed: %s", exc)
        yield {"event": "error", "agent": "pipeline", "detail": str(exc)}
        yield {"event": "pipeline_done", "state": cumulative_state.model_dump(mode="json")}


# --------------------------------------------------------------------------- #
# Parallel Runner Fallback (asyncio.gather)
# --------------------------------------------------------------------------- #


async def _run_parallel(
    state: PipelineState,
    agents: dict[AgentName, Any],
) -> AsyncIterator[dict[str, Any]]:
    # Stage 1: Extraction Agent
    ext_name = AgentName.EXTRACTION
    yield {"event": "agent_start", "agent": ext_name.value}
    try:
        await agents[ext_name].run(state)
    except Exception as exc:
        logger.exception("agent %s failed", ext_name.value)
        yield {"event": "error", "agent": ext_name.value, "detail": str(exc)}
    yield {"event": "agent_done", "agent": ext_name.value, "result": state.model_dump()}

    # Stage 2: Parallel fan-out (Forensics, Policy, History, Ring)
    for name in _PARALLEL_AGENTS:
        yield {"event": "agent_start", "agent": name.value}

    async def _execute_agent(agent_name: AgentName) -> tuple[AgentName, Exception | None]:
        try:
            await agents[agent_name].run(state)
            return agent_name, None
        except Exception as exc:
            logger.exception("agent %s failed", agent_name.value)
            return agent_name, exc

    tasks = [_execute_agent(name) for name in _PARALLEL_AGENTS]
    parallel_results = await asyncio.gather(*tasks)

    for agent_name, err in parallel_results:
        if err is not None:
            yield {"event": "error", "agent": agent_name.value, "detail": str(err)}
        yield {"event": "agent_done", "agent": agent_name.value, "result": state.model_dump()}

    # Stage 3: Verdict Fusion Agent
    verdict_name = AgentName.VERDICT
    yield {"event": "agent_start", "agent": verdict_name.value}
    try:
        await agents[verdict_name].run(state)
    except Exception as exc:
        logger.exception("agent %s failed", verdict_name.value)
        yield {"event": "error", "agent": verdict_name.value, "detail": str(exc)}
    yield {"event": "agent_done", "agent": verdict_name.value, "result": state.model_dump()}

    yield {"event": "pipeline_done", "state": state.model_dump()}