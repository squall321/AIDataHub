# 대규모 검증 하네스 — 각 전문가의 sample_queries 를 '그 사람이 나와야 하는' 라벨로 삼아
# recommend_agents 의 top-1/3/5 정확도를 잰다. 현장표현 사전 ON/OFF 비교에 같은 세트를 쓴다.
import json, random, sys, time, urllib.request, concurrent.futures as cf

BASE = "http://127.0.0.1:8001"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 400
TAG = sys.argv[2] if len(sys.argv) > 2 else "run"
SEED = 20260912

def api(path, payload=None):
    req = urllib.request.Request(BASE + path,
        data=json.dumps(payload).encode() if payload else None,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())

rows = api("/api/agents?limit=2000")
rows = rows if isinstance(rows, list) else (rows.get("result") or rows.get("items") or [])
cases = [(q, r["agent_type"]) for r in rows for q in (r.get("sample_queries") or []) if isinstance(q, str) and len(q) > 5]
random.Random(SEED).shuffle(cases)
cases = cases[:N]
print(f"[{TAG}] 세트 {len(cases)}건 (전체 {sum(len(r.get('sample_queries') or []) for r in rows)}건에서 추출)", flush=True)

def one(case):
    q, want = case
    try:
        d = api("/api/recommend/agents", {"q": q, "top_k": 5})
    except Exception as e:
        return (want, [], repr(e)[:60])
    ags = d.get("agents") or d.get("result") or []
    return (want, [a.get("agent_type") for a in ags][:5], None)

t0 = time.time(); top1 = top3 = top5 = 0; errs = 0; out = []
with cf.ThreadPoolExecutor(max_workers=8) as ex:
    for i, (want, got, err) in enumerate(ex.map(one, cases), 1):
        if err: errs += 1
        top1 += want in got[:1]; top3 += want in got[:3]; top5 += want in got[:5]
        out.append({"want": want, "got": got})
        if i % 100 == 0:
            print(f"  {i}/{len(cases)} · top1 {top1/i:.1%} top3 {top3/i:.1%} top5 {top5/i:.1%} ({time.time()-t0:.0f}s)", flush=True)
n = len(cases)
print(f"[{TAG}] 최종 — top1 {top1/n:.2%} · top3 {top3/n:.2%} · top5 {top5/n:.2%} · 오류 {errs} · {time.time()-t0:.0f}s")
json.dump(out, open(f"bench_{TAG}.json", "w"), ensure_ascii=False)
