"""拉取全部行业(320)+概念(390)指数成分，构建股票→行业/概念反查表。
约 710 次调用，5 req/s 限速，约 3 分钟。产物 data/raw/sector_members.json + 批次质量报告。"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core import fuyao

RAW = os.path.join(os.path.dirname(__file__), "..", "data", "raw")
catalogs = [("industry", "index_industry.json"), ("concept", "index_concept.json")]

members = {"industry": {}, "concept": {}}  # 指数名 → [成分thscode]
fail = {"industry": [], "concept": []}
t0 = time.time()
total_done = 0

for tag, cat_file in catalogs:
    with open(os.path.join(RAW, cat_file), encoding="utf-8") as f:
        items = json.load(f)["data"]["item"]
    for i, it in enumerate(items):
        name, code = it["name"], it["thscode"]
        try:
            d = fuyao.get("/a-share-index/constituents/ths-stock-list", {"thscode": code})
            members[tag][name] = [x["thscode"] for x in d["item"]]
        except Exception as e:
            fail[tag].append({"name": name, "thscode": code, "err": str(e)[:100]})
        total_done += 1
        if total_done % 100 == 0:
            print(f"  {total_done}/710  用时 {time.time()-t0:.0f}s", flush=True)

out = {"generated_ms": int(time.time() * 1000), "members": members,
       "fail": fail, "total_ok": sum(len(v) for v in members.values()),
       "total_fail": len(fail["industry"]) + len(fail["concept"])}
with open(os.path.join(RAW, "sector_members.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False)
print(f"完成: 成功 {out['total_ok']} 失败 {out['total_fail']} 用时 {time.time()-t0:.0f}s", flush=True)
