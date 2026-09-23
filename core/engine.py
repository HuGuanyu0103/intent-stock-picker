"""确定性筛选引擎：条件编译、三值判定、四分组、逐卡试算、贴合度排序、结果 diff。
LLM 不参与任何判定；所有解释数字都来自本模块的判定结果。"""

_OPS = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


def _check(stock, cond):
    """返回 pass / fail / unknown + 实际值。"""
    v = stock.get(cond["metric"])
    if v is None:
        return "unknown", None
    try:
        ok = _OPS[cond["op"]](v, cond["value"])
        return ("pass" if ok else "fail"), v
    except TypeError:
        return "unknown", v


def _fmt(cond, v):
    if v is None:
        return "--"
    if cond["metric"] in ("pe_pct", "vol_pct"):
        return f"{v*100:.0f}%"
    if cond["unit_tier"] == "money" or cond["metric"] == "turn20":
        return f"{v/1e8:.2f} 亿"
    if cond["unit"] == "%":
        return f"{v:.1f}%"
    if isinstance(v, bool):
        return "是" if v else "否"
    return str(round(v, 2) if isinstance(v, float) else v)


def _threshold_text(cond):
    return _fmt(cond, cond["value"])


def trial_counts(matrix, conditions):
    """逐卡在池内的满足计数（include 计满足数；exclude 计将剔除数）。"""
    out = []
    for c in conditions:
        n_pass = n_unknown = 0
        for s in matrix["stocks"].values():
            r, _ = _check(s, c)
            n_pass += r == "pass"
            n_unknown += r == "unknown"
        out.append({"condition_id": c["condition_id"], "pass": n_pass, "unknown": n_unknown,
                    "total": matrix["n"], "polarity": c["polarity"]})
    return out


def _hard_conflicts(conditions):
    """同指标区间无交集的硬冲突（同 polarity 数值条件）。"""
    by_metric = {}
    for c in conditions:
        by_metric.setdefault(c["metric"], []).append(c)
    conflicts = []
    for metric, group in by_metric.items():
        if len(group) < 2 or REG_KIND.get(metric) != "numeric":
            continue
        lo = [c for c in group if c["op"] in (">", ">=")]
        hi = [c for c in group if c["op"] in ("<", "<=")]
        if lo and hi:
            lb, ub = max(c["value"] for c in lo), min(c["value"] for c in hi)
            if lb > ub:
                conflicts.append({"metric": metric, "ids": [c["condition_id"] for c in group]})
    return conflicts


REG_KIND = {  # 与 parser.REGISTRY 保持同步的轻量类型表
    "np_yoy": "numeric", "rev_yoy": "numeric", "pe_pct": "numeric", "vol_pct": "numeric",
    "mdd120": "numeric", "turn20": "numeric", "is_st": "boolean", "is_new": "boolean",
}


def run(matrix, conditions):
    conflicts = _hard_conflicts(conditions)
    inc = [c for c in conditions if c["polarity"] == "include"]
    exc = [c for c in conditions if c["polarity"] == "exclude"]

    groups = {"selected": [], "near_miss": [], "excluded": [],
              "unknown_data": [], "unknown_risk": []}

    for code, s in matrix["stocks"].items():
        checks = []
        inc_fail = inc_unk = exc_hit = risk_unk = data_unk = 0
        for c in conditions:
            r, v = _check(s, c)
            # 内部判定：include pass=合格；exclude pass=触发排除。
            # 投影给用户时，排除条件翻转为正向安全语义（"必须不是 ST"）：
            # pass↔fail、==→≠，unknown 不变；分组仍按内部 r 判定。
            show_r, show_op = r, c["op"]
            if c["polarity"] == "exclude":
                if r == "pass":
                    show_r = "fail"
                elif r == "fail":
                    show_r = "pass"
                show_op = {"==": "≠", "!=": "==", ">": "≤", ">=": "<",
                           "<": "≥", "<=": ">"}.get(c["op"], c["op"])
            checks.append({"condition_id": c["condition_id"], "metric_label": c["metric_label"],
                           "polarity": c["polarity"], "result": show_r, "op": show_op,
                           "actual": _fmt(c, v), "actual_raw": v,
                           "threshold": _threshold_text(c), "unit": c["unit"]})
            if c["polarity"] == "include":
                inc_fail += r == "fail"
                if r == "unknown":
                    inc_unk += 1
                    data_unk += 1
            else:
                if r == "pass":
                    exc_hit += 1
                elif r == "unknown" and c.get("on_unknown") == "quarantine":
                    risk_unk += 1

        rec = {"thscode": code, "name": s["name"], "checks": checks}
        if exc_hit:
            hit = [x for x in checks if x["polarity"] == "exclude" and x["result"] == "fail"]
            rec["reasons"] = [f"{x['metric_label']}：实际为「{x['actual']}」，与你的红线冲突" for x in hit]
            groups["excluded"].append(rec)
        elif risk_unk:
            miss = [x["metric_label"] for x in checks if x["polarity"] == "exclude" and x["result"] == "unknown"]
            rec["reasons"] = miss
            groups["unknown_risk"].append(rec)
        elif data_unk:
            miss = [x["metric_label"] for x in checks if x["polarity"] == "include" and x["result"] == "unknown"]
            rec["reasons"] = miss
            groups["unknown_data"].append(rec)
        elif inc_fail == 0:
            rec["fit"] = _fit_score(s, inc)
            groups["selected"].append(rec)
        elif inc_fail == 1:
            bad = next(x for x in checks if x["polarity"] == "include" and x["result"] == "fail")
            rec["gap"] = f"{bad['metric_label']}：实际 {bad['actual']}，要求 {bad['op']} {bad['threshold']}"
            groups["near_miss"].append(rec)
        # fail>=2 的静默淘汰不在任何展示组（数量在汇总中给出）

    groups["selected"].sort(key=lambda r: r["fit"], reverse=True)
    silent_dropped = matrix["n"] - sum(len(v) for v in groups.values())
    return {"groups": groups, "conflicts": conflicts,
            "counts": {k: len(v) for k, v in groups.items()},
            "silent_dropped": silent_dropped,
            "trials": trial_counts(matrix, conditions)}


def _fit_score(stock, inc):
    """最短板贴合度：所有 include 条件的标准化裕度取最小值（越大越贴合）。"""
    margins = []
    for c in inc:
        v = stock.get(c["metric"])
        if v is None:
            continue
        t = c["value"]
        if c["op"] in ("<", "<="):
            margins.append((t - v) / abs(t) if t else 0.0)
        elif c["op"] in (">", ">="):
            margins.append((v - t) / (abs(t) or 1.0))
        else:
            margins.append(1.0 if v == t else 0.0)
    return min(margins) if margins else 0.0


def diff_codes(prev_result, new_result):
    a = {r["thscode"] for r in prev_result["groups"]["selected"]}
    b = {r["thscode"]: r for r in new_result["groups"]["selected"]}
    added = [b[c] for c in b.keys() - a]
    removed = sorted(a - b.keys())
    return {"added": sorted(added, key=lambda r: r["fit"], reverse=True),
            "removed": removed,
            "count_change": len(b) - len(a)}
