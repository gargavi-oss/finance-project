import asyncio, sys, traceback
from pathlib import Path
from app.schemas.models import PipelineState
from app.orchestration.graph import run_pipeline, _HAS_LANGGRAPH

async def main():
    print("HAS_LANGGRAPH =", _HAS_LANGGRAPH)
    p = Path(sys.argv[1] if len(sys.argv) > 1 else "data/invoices/INV-2026-04182.png")
    state = PipelineState(document_id="test123", filename=p.name, image_path=str(p))
    n = 0
    async for ev in run_pipeline(state):
        n += 1
        t = ev.get("event"); a = ev.get("agent", "")
        if t == "agent_done":
            print(f"  agent_done {a}: status={state.agent_status.get(a)}")
        elif t == "error":
            print(f"  ERROR {a}: {ev.get('detail')}")
        elif t == "pipeline_done":
            print("  pipeline_done")
    print("TOTAL EVENTS =", n)
    print("extraction:", state.extraction.model_dump() if state.extraction else None)
    print("forensics flags:", [f.label for f in state.forensics.flags] if state.forensics else None)
    print("ring matches:", len(state.ring.matches) if state.ring else None, "| ring score:", state.ring.score if state.ring else None)
    print("verdict score:", state.verdict.risk_score if state.verdict else None,
          "| rec:", state.verdict.recommendation if state.verdict else None)
    print("verdict summary:", (state.verdict.summary[:200] if state.verdict else None))

asyncio.run(main())
