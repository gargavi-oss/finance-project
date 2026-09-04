import json, sys, time, urllib.request, urllib.error

BASE = "http://127.0.0.1:8000"


def post_upload(path):
    import os
    boundary = "----dfai"
    with open(path, "rb") as f:
        data = f.read()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{os.path.basename(path)}"\r\n'
        f"Content-Type: image/png\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{BASE}/api/documents",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def get_json(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read())


def stream(doc_id, timeout=60):
    import http.client
    conn = http.client.HTTPConnection("127.0.0.1", 8000, timeout=timeout)
    conn.request("GET", f"/api/documents/{doc_id}/stream")
    resp = conn.getresponse()
    buf = b""
    verdict = None
    start = time.time()
    while time.time() - start < timeout:
        chunk = resp.read(1)
        if not chunk:
            break
        buf += chunk
        if buf.endswith(b"\n\n"):
            line = buf.decode("utf-8", "ignore").strip()
            buf = b""
            if line.startswith("data: "):
                try:
                    ev = json.loads(line[6:])
                except Exception:
                    continue
                t = ev.get("event")
                if t == "agent_start":
                    print(f"    • start {ev.get('agent')}")
                elif t == "agent_done":
                    print(f"    • done  {ev.get('agent')}")
                elif t == "error":
                    print(f"    • ERROR {ev.get('agent')}: {ev.get('detail')}")
                elif t == "pipeline_done":
                    st = ev.get("state", {})
                    v = st.get("verdict") or {}
                    verdict = v
                    print(f"    • pipeline_done score={v.get('risk_score')} rec={v.get('recommendation')}")
                    break
    conn.close()
    return verdict


def main():
    files = sys.argv[1:] or [
        "data/invoices/INV-2026-00181.png",
        "data/invoices/INV-2026-04182.png",
        "data/invoices/ring_alpha_supplies.png",
        "data/invoices/ring_beta_office.png",
    ]
    for f in files:
        print(f"\n=== UPLOAD {f} ===")
        up = post_upload(f)
        did = up["document_id"]
        print(f"  doc_id={did}")
        v = stream(did)
        if v:
            print(f"  VERDICT score={v.get('risk_score')} rec={v.get('recommendation')}")
            print(f"  summary: {v.get('summary','')[:200]}")
            for fnd in v.get("findings", []) or []:
                print(f"    - {fnd.get('agent')}: {fnd.get('headline')} (score={fnd.get('score')})")
        else:
            print("  NO VERDICT (stream ended without pipeline_done)")


if __name__ == "__main__":
    main()
