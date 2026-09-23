"""确定性意图解析：中文模糊表达 → 受注册表约束的条件 JSON（LLM 可选增强的适配位）。

产品立场：翻译规则是预注册、可解释、可在 README 公开的；解析器不发明指标。
环境变量 LLM_API_KEY 存在时可在 llm_enhance 中接入大模型，MVP 默认走规则，行为可复现。
"""
import re

# ── 指标注册表：白名单，parser/engine/UI 共用 ──────────────────────────────
REGISTRY = {
    "np_yoy": {"label": "归母净利润同比增速", "unit": "%", "kind": "numeric",
               "caliber": "2026 中报归母净利润同比上年同期"},
    "rev_yoy": {"label": "营业收入同比增速", "unit": "%", "kind": "numeric",
                "caliber": "2026 中报营业收入同比上年同期"},
    "pe_pct": {"label": "PE-TTM 沪深300内分位", "unit": "", "kind": "numeric",
               "caliber": "当前 PE-TTM 在沪深300 正值样本中的分位（亏损公司不参与，记为不可判定）"},
    "vol_pct": {"label": "年化波动率分位", "unit": "", "kind": "numeric",
                "caliber": "近 60 日日收益年化波动率在池内分位，越低越稳"},
    "mdd120": {"label": "近120日最大回撤", "unit": "%", "kind": "numeric",
               "caliber": "近 120 个交易日收盘价最大回撤幅度"},
    "turn20": {"label": "近20日日均成交额", "unit": "元", "kind": "numeric",
               "caliber": "近 20 个交易日成交额均值"},
    "is_st": {"label": "ST/*ST 风险警示", "unit": "", "kind": "boolean",
              "caliber": "证券名称含 ST 或 *ST"},
    "is_new": {"label": "新股（近6个月内上市）", "unit": "", "kind": "boolean",
               "caliber": "近 400 个自然日窗口内日 K 不足 130 根，近似上市约 6 个月内（扶摇上市日期字段实测为空的替代口径）"},
}

# ── 模糊词翻译词典（专家值：loose / mid / strict）──────────────────────────
# 每个意图落成一个或多个条件；tiers 为三档阈值
PRESETS = [
    {
        "group": "g_improve", "keywords": ["经营改善", "业绩改善", "业绩增长", "盈利改善", "经营好转", "基本面改善"],
        "say": "「经营改善」我按产品预设口径翻译成：最新中报归母净利润与营业收入双双同比正增长。",
        "conditions": [
            {"metric": "np_yoy", "op": ">", "tiers": [0, 0, 5], "unit_tier": None},
            {"metric": "rev_yoy", "op": ">", "tiers": [0, 0, 5], "unit_tier": None},
        ],
    },
    {
        "group": "g_value", "keywords": ["估值合理", "便宜", "低估", "估值便宜", "价格便宜"],
        "say": "「估值合理/便宜」我理解为估值分位低（注意：是估值倍数低，不是绝对股价低），默认要求 PE-TTM 处于沪深300 内 50% 分位以下；亏损公司 PE 无意义，会进不可判定。",
        "conditions": [
            {"metric": "pe_pct", "op": "<=", "tiers": [0.7, 0.5, 0.3], "unit_tier": None},
        ],
    },
    {
        "group": "g_stable", "keywords": ["走势相对稳定", "走势稳定", "稳定", "波动小", "走势平稳", "抗跌"],
        "say": "「走势稳定」我拆成两条：近 60 日波动率处于池内较低分位，且近 120 日最大回撤不超过 15%。",
        "conditions": [
            {"metric": "vol_pct", "op": "<=", "tiers": [0.6, 0.4, 0.25], "unit_tier": None},
            {"metric": "mdd120", "op": "<=", "tiers": [20, 15, 10], "unit_tier": None},
        ],
    },
    {
        "group": "g_active", "keywords": ["活跃", "成交活跃", "流动性好", "成交额大"],
        "say": "「活跃」我用近 20 日日均成交额衡量，默认不低于 5 亿元。",
        "conditions": [
            {"metric": "turn20", "op": ">=", "tiers": [2e8, 5e8, 1e9], "unit_tier": "money"},
        ],
    },
]

EXCLUDE_PRESETS = [
    {"group": "x_st", "keywords": ["st", "ST", "戴帽", "风险警示"],
     "say": "识别为一票否决：剔除 ST/*ST。",
     "metric": "is_st", "op": "==", "value": True, "risk": "hard", "on_unknown": "quarantine"},
    {"group": "x_new", "keywords": ["新股", "次新股", "上市不满一年", "上市不满1年"],
     "say": "识别为一票否决：剔除上市不满一年的新股。",
     "metric": "is_new", "op": "==", "value": True, "risk": "hard", "on_unknown": "quarantine"},
]

# 合规：预测/荐股类意图
FORBIDDEN = ["涨停", "明天涨", "下周涨", "必涨", "买入", "卖出", "推荐", "黑马", "牛股", "翻倍"]
# 行业等暂不支持的集合表达（演示"未理解"一等公民）
UNSUPPORTED_HINTS = ["行业", "板块", "概念", "新能源", "银行", "医药", "龙头", "股性"]


def _condition(preset, cond, quote, polarity, risk="soft", on_unknown="quarantine", value=None):
    meta = REGISTRY[cond["metric"]]
    return {
        "condition_id": f"{preset['group']}_{cond['metric']}",
        "quote": quote,
        "intent_group": preset["group"],
        "metric": cond["metric"],
        "metric_label": meta["label"],
        "op": cond["op"],
        "value": value if value is not None else cond["tiers"][1],
        "tiers": cond.get("tiers"),
        "unit_tier": cond.get("unit_tier"),
        "unit": meta["unit"],
        "polarity": polarity,
        "risk_level": risk,
        "on_unknown": on_unknown,
        "caliber": meta["caliber"],
        "source": "expert_default",
    }


def parse(text):
    """返回 {conditions, narrative, unsupported, warnings, intent_class}。"""
    narrative, warnings, unsupported = [], [], []
    include, excluded = [], []

    if any(w in text for w in FORBIDDEN):
        return {"intent_class": "forbidden",
                "narrative": ["我不能帮你预测涨跌或给出买卖建议。我能做的是把你的选股标准翻译成可检查的数据条件，"
                              "例如「经营改善、估值合理、走势稳定，剔除 ST」。"],
                "conditions": [], "unsupported": [], "warnings": []}

    # 切分意图短语（顿号/逗号/中文分句/空格）
    parts = [p.strip() for p in re.split(r"[，,、。；;\s]+", text) if p.strip()]
    used = set()

    for part in parts:
        low = part
        is_exclude = any(k in low for k in ["剔除", "排除", "不要", "避开", "不看", "去掉", "没有"])
        # 排除类：一个分句可能同时含多条红线（如"剔除ST和上市不满一年的"），不 break
        hit_ex = False
        for ex in EXCLUDE_PRESETS:
            if any(k in part for k in ex["keywords"]) and (is_exclude or ex["metric"] == "is_st" and "st" in low.lower()):
                if ex["group"] not in used:
                    excluded.append(_condition(ex, {"metric": ex["metric"], "op": ex["op"], "tiers": None,
                                                    "unit_tier": None}, part, "exclude", ex["risk"], ex["on_unknown"], ex["value"]))
                    narrative.append(ex["say"])
                    used.add(ex["group"])
                hit_ex = True
        if hit_ex:
            continue
        # 正向类：同一分句既含正向短语又含排除词（如"低估值的不要"）视为口误，
        # 不默默产出正向条件，挂起让用户澄清
        hit = False
        for preset in PRESETS:
            if any(k in part for k in preset["keywords"]):
                if is_exclude:
                    unsupported.append({"quote": part,
                                        "reason": "这句话里既有正向要求又有排除词，我不确定你的真实意图"})
                    narrative.append(f"「{part}」听起来有点矛盾——你是想要低估值的，还是不要低估值的？这条我先挂起，请在卡片上明确。")
                    hit = True
                    break
                if preset["group"] not in used:
                    for cond in preset["conditions"]:
                        include.append(_condition(preset, cond, part, "include"))
                    narrative.append(preset["say"])
                    used.add(preset["group"])
                hit = True
                break
        if not hit:
            if any(h in part for h in UNSUPPORTED_HINTS):
                unsupported.append({"quote": part,
                                    "reason": "行业/概念/题材或「龙头」这类说法暂时没有无争议的量化口径"})
            elif len(part) >= 2:
                unsupported.append({"quote": part, "reason": "我没能把这句话落成数据条件"})

    # 全文级补充检测：空格/连词会把"剔除 ST 和上市不满一年的"切碎，按整句兜底扫描
    has_exclude_word = any(k in text for k in ["剔除", "不要", "排除", "避开", "去掉", "不带", "不看"])
    for ex in EXCLUDE_PRESETS:
        if any(c["metric"] == ex["metric"] for c in excluded):
            continue
        hit = any(k in text for k in ex["keywords"])
        # ST 是明确的风险标识名，即使没有排除词也按排除处理
        if hit and (has_exclude_word or ex["metric"] == "is_st"):
            excluded.append(_condition(ex, {"metric": ex["metric"], "op": ex["op"], "tiers": None,
                                            "unit_tier": None}, ex["keywords"][0], "exclude",
                                       ex["risk"], ex["on_unknown"], ex["value"]))
            narrative.append(ex["say"])

    if not include and not excluded and not unsupported:
        return {"intent_class": "chitchat",
                "narrative": ["我是选股条件助手，试试这样描述：「经营改善、估值合理、走势相对稳定，剔除 ST 和上市不满一年的新股」。"],
                "conditions": [], "unsupported": [], "warnings": []}

    if unsupported:
        for u in unsupported:
            narrative.append(f"关于「{u['quote']}」——{u['reason']}，这一条我先挂起，你可以在卡片上手动指定指标或删除。")

    # 去重保序
    seen, conds = set(), []
    for c in include + excluded:
        if c["condition_id"] not in seen:
            seen.add(c["condition_id"])
            conds.append(c)

    return {"intent_class": "screen" if conds else "unknown",
            "narrative": narrative, "conditions": conds,
            "unsupported": unsupported, "warnings": warnings}
