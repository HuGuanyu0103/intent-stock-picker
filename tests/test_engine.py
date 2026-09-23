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
check("行业类不可量化表达进 unsupported 而非硬编", any("银行" in u["quote"] for u in r4["unsupported"]))
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

print(f"\n{len(FAILS)} 个失败" if FAILS else "\n全部通过")
sys.exit(1 if FAILS else 0)
