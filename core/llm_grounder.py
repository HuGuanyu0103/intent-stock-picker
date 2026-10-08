"""LLM 意图接地：把规则未覆盖的中文长尾表达翻译成受白名单约束的条件 JSON。

边界（与产品立场一致）：
- LLM 只能从 REGISTRY 白名单选 metric，不允许发明指标；
- 输出经严格 JSON 解析 + 值域校验 + 置信度门禁，低置信/表外指标走挂起；
- LLM 不取数、不判定、不输出数字结论，只产出"翻译草案"；
- 任何异常（无 Key/超时/非法 JSON/校验失败）都静默降级为纯规则，不阻断主链路。

配置（OpenAI 兼容协议，DeepSeek 可直接用）：
  LLM_API_KEY  必填（产品自有 LLM 凭证）
  LLM_BASE_URL 默认 https://api.deepseek.com/v1
  LLM_MODEL    默认 deepseek-chat
"""
import json
import os

import requests

from .parser import REGISTRY, _set_condition, SET_META, _sector_tables

# 每个指标允许的算子与值域（LLM 输出越界即拒绝）
_NUMERIC_BOUNDS = {
    "np_yoy": (-100, 500), "rev_yoy": (-100, 500),
    "pe_pct": (0.01, 1.0), "vol_pct": (0.01, 1.0), "mdd120": (1, 90),
    "turn20": (1e7, 5e10), "roe": (-50, 100), "gross_margin": (-50, 100),
    "debt_ratio": (0, 100), "cash_content": (-200, 500),
}
_VALID_OPS = {">", ">=", "<", "<=", "=="}
_CONFIDENCE_GATE = 0.6


def is_configured():
    return bool(os.environ.get("LLM_API_KEY")) or (
        bool(os.environ.get("LLM_MOCK_FILE"))
        and os.path.exists(os.environ.get("LLM_MOCK_FILE", "")))


def mode_label():
    if os.environ.get("LLM_API_KEY"):
        return "llm-live"
    mf = os.environ.get("LLM_MOCK_FILE")
    if mf and os.path.exists(mf):
        return "llm-mock"
    return "rules-only"


def _registry_brief():
    lines = []
    for mid, m in REGISTRY.items():
        bnd = _NUMERIC_BOUNDS.get(mid)
        bnd_txt = f"，值域[{bnd[0]},{bnd[1]}]" if bnd else ""
        lines.append(f"- metric={mid} | {m['label']}({m['unit']}){bnd_txt} | 口径:{m['caliber']}")
    sectors = _sector_tables()
    ind_sample = "、".join(sectors["industry"][:12])
    con_sample = "、".join(sectors["concept"][:8])
    lines.append(f"- metric=industry | 所属行业（op=in，value=行业名数组，可用如：{ind_sample} 等共320个）")
    lines.append(f"- metric=concept | 所属概念（op=in，value=概念名数组，如：{con_sample} 等共390个）")
    return "\n".join(lines)


_SYSTEM = """你是一个严格的「选股条件翻译器」。任务：把投资者口语化的选股表达，翻译成结构化数据条件。

铁律：
1. 只能从下面给定的指标白名单里选 metric，绝对不允许发明任何指标（包括股息率、市值、PE历史分位、PEG、市占率等当前没有的指标）；
2. 白名单无法可靠表达的要求（如"龙头""护城河""高股息""困境反转""有想象空间"），放进 unsupported，不要硬凑；
3. 区分 polarity：用户想要的为 include，明确说"不要/剔除/排除/避开"的为 exclude；注意"不稳定/不便宜"是否定词，要按反义理解，不确定反义是否可靠时放 unsupported；
4. value 必须落在指标值域内，单位与口径严格一致（金额单位是元，百分比是数值如 10 表示10%，分位是 0-1 的小数如 0.5）；
5. 置信度 confidence < 0.6 的翻译一律放 unsupported，宁可挂起也不要猜错；
6. 涉及预测涨跌、买卖建议（涨停、必涨、买入、推荐、黑马等）一律 intent_class=forbidden；
7. 只输出一个 JSON 对象，不要任何解释文字。

输出 JSON 格式：
{
  "intent_class": "screen|forbidden|unknown",
  "conditions": [
    {"quote":"用户原话片段","metric":"白名单指标","op":">=","value":10,
     "polarity":"include|exclude","confidence":0.0-1.0,"reason":"一句话翻译理由"}
  ],
  "unsupported": [{"quote":"原话","reason":"为什么无法落地"}]
}

指标白名单：
"""


def ground(fragments, whole_text):
    """对规则未覆盖的碎片调用 LLM 接地。返回与 parser.parse 同构的 dict。"""
    # 离线 mock 模式（LLM_MOCK_FILE 指向按整句索引的响应文件，用于无网络的混合链路验证）
    mock_file = os.environ.get("LLM_MOCK_FILE")
    if mock_file and os.path.exists(mock_file):
        try:
            with open(mock_file, encoding="utf-8") as f:
                mocks = json.load(f)
            if whole_text in mocks:
                return _validate(mocks[whole_text], fragments)
        except (OSError, ValueError):
            pass
        return None
    if not is_configured():
        return None
    key = os.environ["LLM_API_KEY"]
    base = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
    model = os.environ.get("LLM_MODEL", "deepseek-chat")

    user_msg = (f"用户完整表达：{whole_text}\n\n"
                f"规则解析器未能落地、需要你处理的片段：{json.dumps(fragments, ensure_ascii=False)}\n\n"
                f"请对这些片段（或整句中规则遗漏的意图）做翻译，严格只输出 JSON。")
    try:
        r = requests.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": model, "temperature": 0, "response_format": {"type": "json_object"},
                  "messages": [{"role": "system", "content": _SYSTEM + _registry_brief()},
                               {"role": "user", "content": user_msg}]},
            timeout=25)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        draft = json.loads(content)
    except Exception:
        return None  # 静默降级：任何失败都退回纯规则

    return _validate(draft, fragments)


def _validate(draft, fragments):
    """白名单/值域/置信度三道校验，把 LLM 草案收敛为合法条件。"""
    if draft.get("intent_class") == "forbidden":
        return {"intent_class": "forbidden", "conditions": [], "unsupported": [],
                "narrative": ["我不能帮你预测涨跌或给出买卖建议。"], "_llm": True}
    raw = draft.get("conditions") or []
    conditions, rejected = [], []
    for c in raw:
        mid, op = c.get("metric"), c.get("op")
        conf = c.get("confidence", 0)
        quote = str(c.get("quote", ""))[:40]
        is_set_metric = mid in ("industry", "concept")
        if mid not in REGISTRY and not is_set_metric:
            rejected.append({"quote": quote, "reason": f"LLM 试图使用白名单外指标「{mid}」，已拦截"})
            continue
        if op not in _VALID_OPS and not (is_set_metric and op == "in"):
            rejected.append({"quote": quote, "reason": f"非法算子 {op}"})
            continue
        try:
            value = float(c.get("value"))
        except (TypeError, ValueError):
            # 行业/概念值允许数组
            if mid in ("industry", "concept") and isinstance(c.get("value"), list):
                value = c["value"]
            else:
                rejected.append({"quote": quote, "reason": "阈值无法解析"})
                continue
        if isinstance(value, float) and mid in _NUMERIC_BOUNDS:
            lo, hi = _NUMERIC_BOUNDS[mid]
            if not (lo <= value <= hi):
                rejected.append({"quote": quote, "reason": f"阈值 {value} 超出 {mid} 合理值域"})
                continue
        if conf is not None and conf < _CONFIDENCE_GATE:
            rejected.append({"quote": quote, "reason": f"LLM 置信度仅 {conf}，挂起待确认"})
            continue
        polarity = c.get("polarity", "include") if c.get("polarity") in ("include", "exclude") else "include"
        if mid in ("industry", "concept"):
            cond = _set_condition(quote, mid, [str(v) for v in value][:12], polarity)
        else:
            meta = REGISTRY[mid]
            preset = {"group": f"llm_{mid}",
                      "conditions": [{"metric": mid, "op": op, "tiers": None, "unit_tier": None}]}
            cond = {
                "condition_id": f"llm_{mid}_{polarity}_{abs(hash(quote)) % 100000:05x}",
                "quote": quote, "intent_group": "llm", "metric": mid,
                "metric_label": meta["label"], "op": op, "value": value, "tiers": None,
                "unit_tier": "money" if mid == "turn20" else None, "unit": meta["unit"],
                "polarity": polarity, "risk_level": "soft", "on_unknown": "ignore",
                "caliber": meta["caliber"], "rationale": meta.get("rationale", ""),
                "source": "llm_inferred", "confidence": round(float(conf), 2),
                "reason": str(c.get("reason", ""))[:120],
            }
        conditions.append(cond)
    unsupported = list(draft.get("unsupported") or [])
    unsupported.extend(rejected)
    return {"intent_class": "screen" if conditions else "unknown",
            "conditions": conditions, "unsupported": unsupported,
            "narrative": [f"AI 补充理解：{c.get('reason','') or c['metric_label']}（来自你说的「{c['quote']}」，置信度 {c.get('confidence','--')}）"
                          for c in conditions], "_llm": True}
