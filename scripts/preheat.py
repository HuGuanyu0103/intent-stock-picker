"""沪深300 全池每日预热：成分/代码表/估值快照/行情快照/两期财务指标/180日K线。
串行限速约 4 分钟。产物写入 data/raw/，供 metrics 构建本地指标矩阵。"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core import fuyao

RAW = os.path.join(os.path.dirname(__file__), "..", "data", "raw")


def save(name, obj):
    p = os.path.join(RAW, name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", type=int, default=0, help="只取前 N 只（调试用）")
    args = ap.parse_args()
    os.makedirs(RAW, exist_ok=True)

    print("[1/6] 沪深300 成分股 ...", flush=True)
    cons = fuyao.get("/a-share-index/constituents/ths-stock-list", {"thscode": "000300.SH"})
    pool = cons["item"]
    if args.universe:
        pool = pool[:args.universe]
    save("constituents.json", pool)
    codes = [x["thscode"] for x in pool]
    print(f"  样本池 {len(codes)} 只", flush=True)

    print("[2/6] A股代码表（上市日期/名称）...", flush=True)
    tickers = []
    offset = 0
    while True:
        d = fuyao.get("/meta/tickers/list", {"asset_type": "a-share", "limit": 10000, "offset": offset})
        items = d["item"]
        tickers.extend(items)
        if len(items) < 10000:
            break
        offset += 10000
    save("tickers.json", tickers)

    print("[3/6] 估值快照 + 行情快照 ...", flush=True)
    valuations, prices = [], []
    for batch in chunks(codes, 100):
        valuations.extend(fuyao.get("/a-share/valuations/snapshot", {"thscodes": ",".join(batch)})["item"])
        prices.extend(fuyao.get("/a-share/prices/snapshot", {"thscodes": ",".join(batch)})["item"])
    save("valuations.json", valuations)
    save("prices.json", prices)

    print("[4/6] 两期财务指标（2026中报 / 2025年报）...", flush=True)
    ind = {}
    t0 = time.time()
    for i, c in enumerate(codes):
        row = {}
        for report in ("2026-2", "2025-4"):
            try:
                d = fuyao.get("/a-share/financials/indicators", {"thscode": c, "report": report})
                vals = {}
                for ab in d["abilities"]:
                    for x in ab["indicators"]:
                        vals[x["index_id"]] = x["value"]
                row[report] = vals
            except Exception as e:
                row[report] = {"_error": str(e)[:120]}
        ind[c] = row
        if (i + 1) % 50 == 0:
            print(f"  财务 {i+1}/{len(codes)}  用时 {time.time()-t0:.0f}s", flush=True)
    save("indicators.json", ind)

    print("[5/6] 180 日前复权日K ...", flush=True)
    end = int(datetime(2026, 9, 23).timestamp() * 1000)
    start = int((datetime(2026, 9, 23) - timedelta(days=400)).timestamp() * 1000)
    klines = {}
    t0 = time.time()
    for i, c in enumerate(codes):
        try:
            d = fuyao.get("/a-share/prices/historical",
                          {"thscode": c, "interval": "1d", "start": start, "end": end, "adjust": "forward"})
            klines[c] = d["item"][-130:]
        except Exception as e:
            klines[c] = {"_error": str(e)[:120]}
        if (i + 1) % 50 == 0:
            print(f"  K线 {i+1}/{len(codes)}  用时 {time.time()-t0:.0f}s", flush=True)
    save("klines.json", klines)

    print("[6/6] 完成", flush=True)


if __name__ == "__main__":
    main()
