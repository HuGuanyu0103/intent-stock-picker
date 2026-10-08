"""意图接地评测跑分：规则 vs 混合（规则+LLM）对比。

用法：
  python3 tests/run_eval.py                 # 仅规则（无 LLM Key 时）
  LLM_API_KEY=xxx python3 tests/run_eval.py  # 混合模式（自动调 LLM 补长尾）

指标定义：
  - 接地准确率：expect screen 的用例，期望指标全部落地且无表外幻觉
  - 挂起召回：expect ask_or_unknown 的用例，确实挂起/无正向条件
  - 红线拦截率：forbidden 用例 100% 拦截
  - 幻觉数：输出了白名单外指标（架构上应为 0）
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core import parser

CASES = [json.loads(l) for l in open(os.path.join(os.path.dirname(__file__), "eval_intent.jsonl"), encoding="utf-8")]


def judge(case, res):
    action = case["expect_action"]
    metrics = {c["metric"] for c in res["conditions"]}
    polarity = {f"{c['metric']}:{c['polarity']}" for c in res["conditions"]}
    hallucinated = [m for m in metrics if m not in
                    set(list(parser.REGISTRY.keys()) + ["industry", "concept"])]

    if action == "forbidden":
        return res["intent_class"] == "forbidden", "红线拦截" if res["intent_class"] == "forbidden" else "未拦截"
    if action == "chitchat_or_unknown":
        ok = res["intent_class"] in ("chitchat", "unknown") or not res["conditions"]
        return ok, "闲聊正确分流" if ok else f"误产条件:{metrics}"
    if action == "ask_or_unknown":
        ok = len(res["conditions"]) == 0 and (res["unsupported"] or res["intent_class"] == "unknown")
        return ok, "正确挂起" if ok else f"不该落地却产出:{metrics}"

    # screen：期望指标需全部命中（allow_subset=False 严格；部分长尾用例允许至少命中其一）
    expect = set(case["expect_metrics"])
    missing = expect - metrics
    extra = metrics - expect
    ok = not missing and not hallucinated
    # 极性校验
    for p in case.get("expect_polarity", []):
        if p not in polarity:
            ok = False
    # 期望翻转的（E21）：include 的高波动条件
    for m in case.get("expect_polarity_excl", []):
        if not any(c["metric"] == m and c["polarity"] == "include" and c["op"] in (">", ">=")
                   for c in res["conditions"]):
            ok = False
    detail = []
    if missing: detail.append(f"缺指标{missing}")
    if hallucinated: detail.append(f"幻觉{hallucinated}")
    return ok, "全部命中" if ok else ";".join(detail)


def main():
    from core import llm_grounder
    mode = {"llm-live": "混合(规则+真实LLM)", "llm-mock": "混合(规则+LLM，离线fixture)",
            "rules-only": "纯规则"}[llm_grounder.mode_label()]
    print(f"评测模式：{mode} | 用例 {len(CASES)} 条\n")
    by_layer = {}
    pass_n, halluc_total = 0, 0
    fails = []
    for case in CASES:
        res = parser.parse(case["query"])
        ok, why = judge(case, res)
        halluc_total += len([c for c in res["conditions"]
                             if c["metric"] not in set(list(parser.REGISTRY.keys()) + ["industry", "concept"])])
        layer = case["layer"]
        by_layer.setdefault(layer, [0, 0])
        by_layer[layer][1] += 1
        if ok:
            pass_n += 1
            by_layer[layer][0] += 1
        else:
            fails.append((case["id"], layer, case["query"][:22], why, res.get("llm_used", False)))

    print("分层通过率：")
    for layer, (p, t) in by_layer.items():
        print(f"  {layer:6s} {p}/{t} = {p/t*100:.0f}%")
    print(f"\n总体接地准确率：{pass_n}/{len(CASES)} = {pass_n/len(CASES)*100:.0f}%")
    print(f"白名单外幻觉：{halluc_total}（架构要求恒为 0）")
    if fails:
        print(f"\n未通过 {len(fails)} 条：")
        for fid, layer, q, why, llm in fails:
            print(f"  {fid} [{layer}] 「{q}」→ {why}{'  [LLM已介入]' if llm else ''}")
    print(f"\n判读：{'规则已覆盖大部分场景，剩余失败应靠 LLM 长尾接地补齐' if not os.environ.get('LLM_API_KEY') else ''}")


if __name__ == "__main__":
    main()
