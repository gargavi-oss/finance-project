import asyncio, httpx, json
from app.main import app
from app.db.database import init_db

async def run_one(c, path, label):
    name = path.split("/")[-1]
    with open(path, "rb") as f:
        r = await c.post("/api/documents", files={"file": (name, f, "image/png")})
    print(label, "upload", r.status_code, flush=True)
    doc_id = r.json()["document_id"]
    verdict = None
    async with c.stream("GET", f"/api/documents/{doc_id}/stream") as s:
        async for line in s.aiter_lines():
            if not line.startswith("data:"): continue
            ev = json.loads(line[5:])
            if ev["event"] == "agent_done":
                st = ev["result"].get("agent_status", {})
                print(f"  {label} {ev['agent']} -> {st.get(ev['agent'])}", flush=True)
            elif ev["event"] == "pipeline_done":
                verdict = ev["state"].get("verdict") or {}
    print(f"  >> {label}: risk={verdict.get('risk_score')} rec={verdict.get('recommendation')}", flush=True)
    print("     summary:", (verdict.get('summary') or '')[:150], flush=True)

async def main():
    await init_db()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60) as c:
        await run_one(c, "./data/invoices/INV-2026-04182.png", "tampered")

asyncio.run(main())
print("DONE", flush=True)
