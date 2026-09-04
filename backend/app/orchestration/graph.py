"""LangGraph orchestration of the six-agent pipeline.

If LangGraph is importable we build a real async ``StateGraph`` that calls
each agent in order. If not (eg. the user hasn't installed it yet) we fall
back to a tiny sequential runner that yields the same SSE events. Either
way, callers get the same ``PipelineState`` back and the UI sees the same
event stream.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator, Awaitable, Callable

from app.agents import (
    ExtractionAgent,
    ForensicsAgent,
    HistoryAgent,
    PolicyAgent,
    RingDetectionAgent,
    VerdictAgent,
)
from app.schemas.models import AgentName, PipelineState

logger = logging.getLogger(__name__)

try:
    from langgraph.graph import END, StateGraph  # type: ignore

    _HAS_LANGGRAPH = True
except Exception:  # pragma: no cover - optional
    _HAS_LANGGRAPH = False


AgentRunner = Callable[[PipelineState], Awaitable[PipelineState]]


# Pipeline order — matches Section 6 (Methodology) of the proposal:
# Extraction must run first; Forensics, Policy, History, and Ring can run in
# parallel; Verdict must run last. We model them sequentially in this MVP for
# clarity — moving to a parallel fan-out is a one-line StateGraph edit.
_PIPELINE_ORDER: list[AgentName] = [
    AgentName.EXTRACTION,
    AgentName.FORENSICS,
    AgentName.POLICY,
    AgentName.HISTORY,
    AgentName.RING,
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
    """Yield SSE events while running the six-agent pipeline."""
    agents = _build_agents()
    if _HAS_LANGGRAPH:
        graph = _build_state_graph(agents)
        compiled = graph.compile()
        async for event in _run_with_langgraph(compiled, state, agents):
            yield event
        return

    async for event in _run_sequential(state, agents):
        yield event


# --------------------------------------------------------------------------- #
# LangGraph state machine
# --------------------------------------------------------------------------- #


def _build_state_graph(agents: dict[AgentName, Any]):  # pragma: no cover - exercised when langgraph installed
    """Build a 6-node LangGraph where each node wraps its agent's async run()."""

    def make_node(name: AgentName):
        async def _node(state_dict: dict) -> dict:
            # Re-hydrate PipelineState from the dict LangGraph passes us.
            ps = PipelineState.model_validate(state_dict)
            ps.agent_status[name.value] = "running"
            try:
                await agents[name].run(ps)
            except Exception as exc:
                logger.exception("agent %s failed", name.value)
                ps.errors[name.value] = str(exc)
                ps.agent_status[name.value] = "error"
            return ps.model_dump()

        _node.__name__ = f"node_{name.value}"
        return _node

    graph = StateGraph(dict)
    for name in _PIPELINE_ORDER:
        graph.add_node(name.value, make_node(name))
    graph.set_entry_point(AgentName.EXTRACTION.value)
    for prev, nxt in zip(_PIPELINE_ORDER, _PIPELINE_ORDER[1:]):
        graph.add_edge(prev.value, nxt.value)
    graph.add_edge(AgentName.VERDICT.value, END)
    return graph


async def _run_with_langgraph(  # pragma: no cover - exercised when langgraph installed
    compiled,
    state: PipelineState,
    agents: dict[AgentName, Any],
) -> AsyncIterator[dict[str, Any]]:
    """Use LangGraph's async API to stream progress events."""
    for name in _PIPELINE_ORDER:
        yield {"event": "agent_start", "agent": name.value}

    try:
        # LangGraph's ainvoke returns the final state dict.
        final = await compiled.ainvoke(state.model_dump())
        state_dict = final
        # After the graph runs, the state_dict holds the final pipeline
        # snapshot — re-hydrate so we can yield agent_done events with the
        # latest payload per agent.
        ps = PipelineState.model_validate(state_dict)
        for name in _PIPELINE_ORDER:
            yield {"event": "agent_done", "agent": name.value, "result": ps.model_dump()}
        yield {"event": "pipeline_done", "state": ps.model_dump()}
    except Exception as exc:
        logger.exception("LangGraph run failed")
        yield {"event": "error", "agent": "pipeline", "detail": str(exc)}
        yield {"event": "pipeline_done", "state": state.model_dump()}


# --------------------------------------------------------------------------- #
# Manual fallback (no LangGraph installed)
# --------------------------------------------------------------------------- #


async def _run_sequential(
    state: PipelineState,
    agents: dict[AgentName, Any],
) -> AsyncIterator[dict[str, Any]]:
    for name in _PIPELINE_ORDER:
        agent = agents[name]
        yield {"event": "agent_start", "agent": name.value}
        try:
            await agent.run(state)
        except Exception as exc:
            logger.exception("agent %s failed", name.value)
            yield {"event": "error", "agent": name.value, "detail": str(exc)}
        yield {"event": "agent_done", "agent": name.value, "result": state.model_dump()}
    yield {"event": "pipeline_done", "state": state.model_dump()}