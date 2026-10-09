"""引擎与解析器离线测试：python3 tests/test_engine.py（零第三方依赖）。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core import parser as P
from core import engine as E

FAILS = []


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        FAILS.append(name)


# ── 解析器 ─────────────────────────────────────────────────────────────
r = P.parse("经营改善、估值合理、走势相对稳定，剔除 ST 和上市不满一年的")
ids = [c["condition_id"] for c in r["conditions"]]
check("标准三连解析为 7 条条件", len(r["conditions"]) == 7)
check("经营改善拆为净利+营收两条", {"g_improve_np_yoy", "g_improve_rev_yoy"} <= set(ids))
check("ST 识别为排除", any(c["metric"] == "is_st" and c["polarity"] == "exclude" for c in r["conditions"]))
check("新股红线被整句兜底识别", any(c["metric"] == "is_new" and c["polarity"] == "exclude" for c in r["conditions"]))

r2 = P.parse("低估值、业绩增长、成交活跃，不要ST")
check("活跃映射为成交额条件", any(c["metric"] == "turn20" for c in r2["conditions"]))
check("条件取值全部落在注册表白名单", all(c["metric"] in P.REGISTRY for c in r2["conditions"]))

r3 = P.parse("推荐下周涨停的黑马")
check("荐股/预测意图被拦截", r3["intent_class"] == "forbidden")

r4 = P.parse("便宜的好公司，剔除银行板块")
check("「剔除银行板块」生成行业排除条件",
      any(c["metric"] == "industry" and c["polarity"] == "exclude" for c in r4["conditions"]))
check("便宜映射为估值分位而非绝对股价", any(c["metric"] == "pe_pct" for c in r4["conditions"]))

r5 = P.parse("低估值的不要")
# 疑似口误：出现排除词但同时是正向价值短语 → 至少不得默默产出正向 include
check("口误表达不产出正向估值条件",
      all(not (c["metric"] == "pe_pct" and c["polarity"] == "include") for c in r5["conditions"]))

# ── 引擎三值/四分组（合成矩阵）──────────────────────────────────────────
matrix = {"n": 4, "stocks": {
    "A": {"name": "甲", "np_yoy": 10, "rev_yoy": 8, "pe_pct": 0.2, "vol_pct": 0.2, "mdd120": 8,
          "turn20": 6e8, "is_st": False, "is_new": False, "suspended": False},
    "B": {"name": "乙", "np_yoy": 10, "rev_yoy": 8, "pe_pct": 0.52, "vol_pct": 0.2, "mdd120": 8,
          "turn20": 6e8, "is_st": False, "is_new": False, "suspended": False},
    "C": {"name": "丙", "np_yoy": 10, "rev_yoy": 8, "pe_pct": 0.2, "vol_pct": 0.2, "mdd120": 8,
          "turn20": 6e8, "is_st": True, "is_new": False, "suspended": False},
    "D": {"name": "丁", "np_yoy": None, "rev_yoy": 8, "pe_pct": 0.2, "vol_pct": 0.2, "mdd120": 8,
          "turn20": 6e8, "is_st": False, "is_new": False, "suspended": False},
}}
conds = P.parse("经营改善、估值合理、走势稳定，剔除ST")["conditions"]
res = E.run(matrix, conds)
check("A 全满足入选", any(x["thscode"] == "A" for x in res["groups"]["selected"]))
check("B 仅卡 PE 进差一点", any(x["thscode"] == "B" and "PE" in x["gap"] for x in res["groups"]["near_miss"]))
check("C 触发 ST 排除组", any(x["thscode"] == "C" for x in res["groups"]["excluded"]))
# 排除条件投影翻转：用户视角"必须不是 ST"
a_checks = next(x for x in res["groups"]["selected"] if x["thscode"] == "A")["checks"]
st_show = next(x for x in a_checks if x["metric_label"].startswith("ST"))
check("入选非ST股的ST行显示为✔满足且要求为「必须为否」",
      st_show["result"] == "pass" and st_show["op"] == "必须为" and st_show["threshold"] == "否")
c_rec = next(x for x in res["groups"]["excluded"] if x["thscode"] == "C")
st_fail = next(x for x in c_rec["checks"] if x["metric_label"].startswith("ST"))
check("被排除ST股的ST行显示为✘且原因点出红线冲突", st_fail["result"] == "fail" and "红线" in c_rec["reasons"][0])
check("D 财务缺失进数据不足", any(x["thscode"] == "D" for x in res["groups"]["unknown_data"]))
check("入选按贴合度降序", all(res["groups"]["selected"][i]["fit"] >= res["groups"]["selected"][i+1]["fit"]
                         for i in range(len(res["groups"]["selected"])-1)))

# 硬冲突
cc = [{"condition_id": "x1", "metric": "pe_pct", "op": "<=", "value": 0.3},
      {"condition_id": "x2", "metric": "pe_pct", "op": ">=", "value": 0.8}]
check("无交集阈值判为硬冲突", len(E._hard_conflicts([
    {**cc[0], **{"metric_label": "", "tiers": None, "unit_tier": None, "unit": "", "polarity": "include"}},
    {**cc[1], **{"metric_label": "", "tiers": None, "unit_tier": None, "unit": "", "polarity": "include"}}])) == 1)

# diff
m2 = {"n": 4, "stocks": dict(matrix["stocks"])}
res2 = E.run(m2, [c for c in conds if c["metric"] != "is_st"])
d = E.diff_codes(res, res2)
check("diff 能识别新增/掉出", "C" in [x["thscode"] for x in d["added"]])

# ── 否定词处理（用户实抓问题类的回归锁）──────────────────────────────────
n1 = P.parse("不稳定的股票")
n1c = n1["conditions"]
check("「不稳定」翻转为高波动/大回撤 include 条件",
      any(c["metric"] == "vol_pct" and c["op"] == ">=" and c["polarity"] == "include" for c in n1c)
      and any(c["metric"] == "mdd120" and c["op"] == ">=" for c in n1c))
n2 = P.parse("不要不稳定的")
check("「不要不稳定」翻转为 exclude 高波动",
      all(c["polarity"] == "exclude" for c in n2["conditions"]) and len(n2["conditions"]) == 2)
n3 = P.parse("不便宜的")
check("「不便宜」反直觉，挂起而非反向硬选", len(n3["conditions"]) == 0 and len(n3["unsupported"]) == 1)
n4 = P.parse("业绩没增长的")
check("「没增长」挂起澄清", len(n4["conditions"]) == 0 and len(n4["unsupported"]) == 1)
n5 = P.parse("走势稳定，不抗跌的不要")
check("同句正反义共存不串味",
      any(c["metric"] == "vol_pct" and c["polarity"] == "include" and c["op"] == "<=" for c in n5["conditions"])
      and any(c["polarity"] == "exclude" for c in n5["conditions"]))

# ── 行业集合条件（合成矩阵；无预热数据时 parser 词表为空，用直接构造条件测引擎）──
set_matrix = {"n": 3, "stocks": {
    "Y": {"name": "银行甲", "industries": ["银行", "国有大型银行"], "concepts": []},
    "Z": {"name": "白酒乙", "industries": ["白酒"], "concepts": []},
    "W": {"name": "无行业", "industries": [], "concepts": []},
}}
inc_set = [{"condition_id": "s1", "metric": "industry", "op": "in", "value": ["银行"],
            "metric_label": "所属行业", "tiers": None, "unit_tier": None, "unit": "", "polarity": "include"}]
sr = E.run(set_matrix, inc_set)
check("行业 in：银行股入选、白酒不入选",
      any(x["thscode"] == "Y" for x in sr["groups"]["selected"])
      and not any(x["thscode"] == "Z" for x in sr["groups"]["selected"]))
exc_set = [{**inc_set[0], "polarity": "exclude"}]
sr2 = E.run(set_matrix, exc_set)
check("行业 exclude：银行股进排除组且投影为「不属于」红线",
      any(x["thscode"] == "Y" for x in sr2["groups"]["excluded"]))
y_show = next(c for x in sr2["groups"]["excluded"] if x["thscode"] == "Y"
              for c in x["checks"] if c["condition_id"] == "s1")
check("行业排除证据显示「不属于」", y_show["op"] == "不属于" and y_show["result"] == "fail")
check("无行业归属：include 时进数据不足；exclude 软偏好 ignore 时不影响入选",
      any(x["thscode"] == "W" for x in sr["groups"]["unknown_data"])
      and any(x["thscode"] == "W" for x in sr2["groups"]["selected"]))

print(f"\n{len(FAILS)} 个失败" if FAILS else "\n全部通过")
sys.exit(1 if FAILS else 0)
