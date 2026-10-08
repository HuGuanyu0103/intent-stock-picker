"""从 data/raw 预热数据构建本地指标矩阵（全部为确定性计算）。"""
import json
import os
import math
from datetime import datetime, date

RAW = os.path.join(os.path.dirname(__file__), "..", "data", "raw")


def _load(name):
    with open(os.path.join(RAW, name), encoding="utf-8") as f:
        return json.load(f)


def _f(v):
    if v is None or v == "":
        return None
    try:
        x = float(v)
        return None if math.isnan(x) else x
    except (TypeError, ValueError):
        return None


def _pct_rank(values):
    """返回 {索引: 分位}，None 不参与；值越小分位越低。"""
    valid = [(i, v) for i, v in enumerate(values) if v is not None]
    order = sorted(valid, key=lambda t: t[1])
    n = len(order)
    ranks = {}
    for pos, (i, _) in enumerate(order):
        ranks[i] = (pos + 1) / n if n else None
    return ranks


def build_matrix():
    constituents = _load("constituents.json")
    tickers = {t["thscode"]: t for t in _load("tickers.json")}
    valuations = {v["thscode"]: v for v in _load("valuations.json")}
    prices = {p["thscode"]: p for p in _load("prices.json")}
    indicators = _load("indicators.json")
    klines = _load("klines.json")

    # 行业/概念反查表（来自 710 个指数成分预热；文件缺失时降级为空）
    stock_industries, stock_concepts = {}, {}
    sec = {"industry": {}, "concept": {}}
    sector_path = os.path.join(RAW, "sector_members.json")
    if os.path.exists(sector_path):
        sec = _load("sector_members.json")["members"]
        for idx_name, codes in sec.get("industry", {}).items():
            for code in codes:
                stock_industries.setdefault(code, []).append(idx_name)
        for idx_name, codes in sec.get("concept", {}).items():
            for code in codes:
                stock_concepts.setdefault(code, []).append(idx_name)

    stocks = {}
    for c in constituents:
        code, name = c["thscode"], c["name"]
        row = {"thscode": code, "name": name}
        # industry=最细分行业（展示用）；industries=全部归属（行业条件判定用）
        inds = stock_industries.get(code, [])
        row["industry"] = min(inds, key=lambda n: len(sec["industry"][n])) if inds else None
        row["industries"] = inds
        row["concepts"] = stock_concepts.get(code, [])

        # 交易过滤类
        row["is_st"] = ("ST" in name.upper())
        # 扶摇代码表 list_date 实测全为 null，改用 K 线根数：400 自然日窗口内
        # 不足 130 根日 K ≈ 近 6 个月内上市（口径已在 README 注明）
        row["is_new"] = None
        snap = prices.get(code)
        row["suspended"] = not (snap and snap.get("last_price"))

        # 估值快照
        val = valuations.get(code, {})
        row["pe_ttm"] = _f(val.get("pe_ttm"))
        row["pb_mrq"] = _f(val.get("pb_mrq"))

        # 财务（最新中报）
        ind = indicators.get(code, {})
        cur = ind.get("2026-2") or {}
        if cur.get("_error"):
            cur = {}
        row["np_yoy"] = _f(cur.get("calculate_parent_holder_net_profit_yoy_growth_ratio"))
        row["rev_yoy"] = _f(cur.get("calculate_operating_income_yoy_growth_ratio"))
        row["roe"] = _f(cur.get("index_weighted_avg_roe"))
        row["gross_margin"] = _f(cur.get("sale_gross_margin"))
        row["net_margin"] = _f(cur.get("sale_net_interest_ratio"))
        row["debt_ratio"] = _f(cur.get("assets_debt_ratio"))
        row["cash_content"] = _f(cur.get("net_profit_cash_content"))

        # K线类指标
        kl = klines.get(code)
        bars = kl if isinstance(kl, list) else []
        if bars:
            row["is_new"] = len(bars) < 130
        closes = [b["close_price"] for b in bars if b.get("close_price")]
        turnovers = [b["turnover"] for b in bars if b.get("turnover") is not None]
        row["turn20"] = sum(turnovers[-20:]) / min(20, len(turnovers)) if turnovers else None
        if len(closes) >= 30:
            rets = [(closes[i] / closes[i - 1] - 1) for i in range(max(1, len(closes) - 60), len(closes))]
            mean = sum(rets) / len(rets)
            var = sum((r - mean) ** 2 for r in rets) / max(1, len(rets) - 1)
            row["vol_ann"] = math.sqrt(var) * math.sqrt(242) * 100
        else:
            row["vol_ann"] = None
        if len(closes) >= 30:
            win = closes[-120:]
            peak = win[0]
            mdd = 0.0
            for px in win:
                peak = max(peak, px)
                mdd = min(mdd, px / peak - 1)
            row["mdd120"] = abs(mdd) * 100
        else:
            row["mdd120"] = None

        stocks[code] = row

    # 池内横截面分位（PE 仅正值参与；波动率/回撤全池）
    codes = list(stocks)
    pe_vals = [stocks[c]["pe_ttm"] if (stocks[c]["pe_ttm"] or 0) > 0 else None for c in codes]
    for c, rk in zip(codes, (_pct_rank(pe_vals).get(i) for i in range(len(codes)))):
        stocks[c]["pe_pct"] = rk
    # 全部数值指标的池内横截面分位（用于跨指标可比的贴合度排序）
    pct_specs = [("vol_ann", "vol_pct"), ("mdd120", "mdd_pct"),
                 ("np_yoy", "np_pct"), ("rev_yoy", "rev_pct"), ("turn20", "turn_pct"),
                 ("roe", "roe_pct"), ("gross_margin", "gm_pct"),
                 ("debt_ratio", "debt_pct"), ("cash_content", "cc_pct")]
    for metric, key in pct_specs:
        vals = [stocks[c].get(metric) for c in codes]
        ranks = _pct_rank(vals)
        for i, c in enumerate(codes):
            stocks[c][key] = ranks.get(i)

    # 批次质量：关键指标缺失率 + 行业覆盖率，超阈值标 degraded
    n = len(stocks)
    unknown_rates = {}
    for metric in ("np_yoy", "rev_yoy", "pe_pct", "vol_pct", "mdd_pct", "turn_pct",
                   "roe", "gross_margin", "debt_ratio", "cash_content"):
        miss = sum(1 for s in stocks.values() if s.get(metric) is None)
        unknown_rates[metric] = round(miss / n, 3) if n else 1.0
    industry_cov = round(sum(1 for s in stocks.values() if s.get("industry")) / n, 3) if n else 0
    degraded = any(rate > 0.15 for rate in unknown_rates.values()) or industry_cov < 0.9

    # 数据时点：快照 timestamp 实测多为 null，改用 K 线最后交易日（真实可靠）
    last_dates = []
    for kl in klines.values():
        if isinstance(kl, list) and kl:
            last_dates.append(kl[-1].get("date_ms"))
    as_of = (datetime.fromtimestamp(max(last_dates) / 1000).strftime("%Y-%m-%d") + " 收盘（盘后预热）"
             if last_dates else "数据时点未知（预热快照）")
    return {"as_of": as_of, "n": n, "stocks": stocks,
            "health": {"degraded": degraded, "unknown_rates": unknown_rates,
                       "industry_coverage": industry_cov}}


if __name__ == "__main__":
    m = build_matrix()
    out = os.path.join(os.path.dirname(__file__), "..", "data", "matrix.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False)
    print(f"matrix built: {m['n']} stocks, as_of={m['as_of']}")
