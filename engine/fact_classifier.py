"""Facts 原子类型分类器 — 规则基，零 LLM 调用。

5 种类型按衰减速率从快到慢：
  EPISODIC  → 0.05/天（20天到底）
  PLANNED   → 0.03/天（33天到底）
  PREFERENCE→ 0.01/天（100天）
  RELATIONAL→ 0.002/天（~500天）
  FACTUAL   → 0.001/天（~1000天，几乎不衰减）
"""

import re

# ─── 类型常量 ───────────────────────────────────────────────
EPISODIC = "EPISODIC"
FACTUAL = "FACTUAL"
RELATIONAL = "RELATIONAL"
PREFERENCE = "PREFERENCE"
PLANNED = "PLANNED"

# ─── 每种类型的衰减速率 ───────────────────────────────────────
DECAY_RATES: dict[str, float] = {
    EPISODIC: 0.05,      # 20天到底
    FACTUAL: 0.001,      # ~1000天（几乎不衰减）
    RELATIONAL: 0.002,   # ~500天
    PREFERENCE: 0.01,    # 100天
    PLANNED: 0.03,       # ~33天
}

# ─── 分类规则（正则）─────────────────────────────────────────
_TIME_WORDS = re.compile(
    r'今天|明天|昨天|前天|后天|上周|下周|这周|本周|周末|'
    r'上个月|下个月|这个月|今年|去年|明年|'
    r'最近|刚才|刚刚|马上|等会|一会|待会|'
    r'早上|中午|晚上|今晚|昨晚|'
    r'\d+月\d+[日号]|\d+点|\d+号'
)

_PLAN_WORDS = re.compile(
    r'打算|准备|计划|想要去|要去|即将|'
    r'之后会|以后要|以后会|将来|'
    r'预计|预定|约了|报名|定了'
)

_RELATION_WORDS = re.compile(
    r'认识|朋友|同事|同学|室友|舍友|'
    r'男朋友|女朋友|老婆|老公|丈夫|妻子|'
    r'兄弟|姐妹|哥哥|弟弟|姐姐|妹妹|'
    r'爸|妈|父亲|母亲|儿子|女儿|'
    r'对象|cp|情侣|基友|闺蜜'
)

_PREFERENCE_WORDS = re.compile(
    r'喜欢|讨厌|爱[^情人]|恨|偏好|'
    r'想要|不想|最爱|不喜欢|'
    r'感兴趣|无聊|烦|迷|沉迷|'
    r'推荐|安利|种草|拔草'
)

_FACTUAL_WORDS = re.compile(
    r'^是|^在|属于|来自|毕业于|住在|'
    r'叫做|名字是|名叫|ID是|'
    r'职业|工作是|学的是|专业'
)


# ─── 公开 API ────────────────────────────────────────────────

def classify_fact(subject: str, predicate: str, obj: str) -> str:
    """根据三元组内容分类 fact 类型。

    优先级: PLANNED > EPISODIC > RELATIONAL > PREFERENCE > FACTUAL
    默认（无法判断）→ FACTUAL（保守策略，不轻易衰减）

    Returns:
        EPISODIC / FACTUAL / RELATIONAL / PREFERENCE / PLANNED
    """
    full_text = f"{predicate} {obj}"

    if _PLAN_WORDS.search(full_text):
        return PLANNED
    if _TIME_WORDS.search(full_text):
        return EPISODIC
    if _RELATION_WORDS.search(predicate) or _RELATION_WORDS.search(obj):
        return RELATIONAL
    if _PREFERENCE_WORDS.search(predicate):
        return PREFERENCE
    if _FACTUAL_WORDS.search(predicate):
        return FACTUAL

    # 默认保守策略
    return FACTUAL


def get_decay_rate(fact_type: str, base_rate: float = 0.005) -> float:
    """获取指定类型的衰减速率。

    Args:
        fact_type: 类型标识
        base_rate: 未知类型时的兜底速率
    """
    return DECAY_RATES.get(fact_type, base_rate)
