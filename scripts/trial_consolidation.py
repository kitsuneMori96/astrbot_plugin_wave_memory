#!/usr/bin/env python3
"""Phase 0b 试跑脚本：直接调 DeepSeek API 验证新 prompt 的 knowledge_group / temporal_tier 打标质量。

用法：
  python3 scripts/trial_consolidation.py --db /path/to/wave_memory.db
"""
import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
import urllib.request

TRIAL_DB = "/tmp/wave_memory_trial.db"
SAMPLE_SIZE = 30
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"

# 新 prompt（与 consolidation.py 同步）
PROMPT_TEMPLATE = """从以下群聊消息中提取结构化知识。

消息格式: [昵称(QQ号) 时间] 内容

---
{conversation}
---

核心判据（每条 facts 都必须过这一刀）：
这条知识会不会改变她下次说话的方式？不会就不抽。

四组分类（knowledge_group）：
G1 关于人 — 身份、偏好、能力、负知识
  ✅ "赣州南康人"  "喜欢gal"  "讨厌被叫全名"  "会画画"
  ❌ "他刚才在聊聚餐" — 那是对话内容，不是知识
G2 关于关系 — 谁和谁什么关系、什么张力
  ✅ "A和B是情侣"  "C和D不对付"
G3 关于场域 — 梗、黑话、潜规则、历史事件
  ✅ "这个群'锐评'的用法"  "上次那个大争吵"
G4 关于她 — 别人怎么评价她、什么话有效、被叫的模式
  ✅ "有人说她可爱"  "锐评被笑了"
  ❌ "她刚回复了什么" — 那是流水，不改变行为

时效分层（temporal_tier）：
- identity：持久不变（籍贯/性格/能力/偏好）→ 不过期
- status：短期有效（在考研/感冒了）→ 30天过期
- event：一次性行为（问了外卖/说今天累）→ 写库时跳过不存，但请照样标注

请输出 JSON（不要输出其他内容）：
{{
  "summary": "一句话概括这段对话的核心内容",
  "topics": ["话题1", "话题2"],
  "facts": [
    {{
      "subject": "人名",
      "predicate": "谓词",
      "object": "内容",
      "knowledge_group": 1,
      "temporal_tier": "identity"
    }}
  ],
  "relations": [
    {{"source": "人物或话题", "target": "人物/话题/事物", "type": "关系类型"}}
  ],
  "social": [
    {{"person_a": "人名A", "person_b": "人名B", "relation": "朋友/互怼/师徒/情侣/对立/合作/认识"}}
  ],
  "nicknames": [
    {{"person": "QQ号或当前昵称", "called": "群友给的绰号或别称"}}
  ]
}}

规则：
- topics 最多 3 个，用简短名词短语
- facts 最多 5 个，必须是三元组格式，subject 必须包含具体人名
- knowledge_group 必填：1=关于人，2=关系，3=梗/场域，4=关于她；不确定时默认 1
- temporal_tier 必填：identity=持久，status=短期，event=一次性；不确定时默认 status
- 关系知识（G2）的 subject 和 object 可以都是人名
- relations 描述 topics/人物 之间的关联，最多 4 条
- type 从以下选择：discusses/mentions/decides/supports/opposes/reacts_to/creates/uses/knows/relates_to
- social 描述对话中体现的人际关系（最多 2 条，没有则留空数组）
- nicknames 提取对话中出现的绰号/别称，最多 3 条，没有则留空数组
- 如果对话是无意义灌水，summary 写"日常灌水"，其他字段留空数组
- 直接输出 JSON，不要 markdown 代码块"""


def find_sample(db_path, keyword_groups, label):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    all_rows = []
    for keywords in keyword_groups:
        conditions = " OR ".join([f"content LIKE '%{kw}%'" for kw in keywords])
        rows = conn.execute(f"""
            SELECT id, group_id, sender_id, sender_name, content, timestamp
            FROM memories WHERE {conditions}
            ORDER BY timestamp ASC LIMIT 50
        """).fetchall()
        all_rows.extend(rows)
    conn.close()
    if not all_rows:
        print(f"  ⚠ 未找到匹配 '{label}' 的消息")
        return []
    all_rows.sort(key=lambda r: r["timestamp"])
    if len(all_rows) > SAMPLE_SIZE:
        mid = len(all_rows) // 2
        all_rows = all_rows[mid - SAMPLE_SIZE // 2 : mid + SAMPLE_SIZE // 2]
    return all_rows


def format_conversation(rows):
    lines = []
    for r in rows:
        ts = time.strftime("%m-%d %H:%M", time.localtime(r["timestamp"]))
        lines.append(f"[{r['sender_name']}({r['sender_id']}) {ts}] {r['content']}")
    return "\n".join(lines)


def call_deepseek(prompt):
    payload = json.dumps({
        "model": DEEPSEEK_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 2000,
    }).encode("utf-8")
    req = urllib.request.Request(
        DEEPSEEK_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"]


def parse_response(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.lstrip("```json").lstrip("```").rstrip("```").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        import re
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
    return None


def run_trial(db_path):
    print(f"DeepSeek API: {DEEPSEEK_URL}")
    print(f"Model: {DEEPSEEK_MODEL}")

    # 1. 找样本
    print("\n--- 样本 1: 身份密集段 ---")
    s1 = find_sample(db_path, [
        ["生日", "南康", "赣州", "喜欢", "讨厌", "会", "擅长", "我是", "老家", "在读"],
    ], "身份密集")
    for r in s1[:5]:
        print(f"  [{r['sender_name']}] {r['content'][:70]}")
    print(f"  ... 共 {len(s1)} 条")

    print("\n--- 样本 2: 日常灌水段 ---")
    s2 = find_sample(db_path, [
        ["哈哈", "草", "666", "笑", "绝了", "笑死"],
    ], "灌水")
    for r in s2[:5]:
        print(f"  [{r['sender_name']}] {r['content'][:70]}")
    print(f"  ... 共 {len(s2)} 条")

    print("\n--- 样本 3: 评价bot段 ---")
    s3 = find_sample(db_path, [
        ["茉莉", "可爱", "好用", "厉害", "蠢", "萌"],
    ], "评价bot")
    s3 = [r for r in s3 if r["sender_name"] not in ("bot", "Bot")][:SAMPLE_SIZE]
    for r in s3[:5]:
        print(f"  [{r['sender_name']}] {r['content'][:70]}")
    print(f"  ... 共 {len(s3)} 条")

    samples = [("身份密集", s1), ("日常灌水", s2), ("评价bot", s3)]
    all_results = []

    for label, rows in samples:
        if not rows:
            print(f"\n⏭ 跳过 '{label}'（无数据）")
            continue

        print(f"\n{'='*50}")
        print(f"试跑: {label} ({len(rows)} 条消息)")
        print(f"{'='*50}")

        conv = format_conversation(rows)
        prompt = PROMPT_TEMPLATE.format(conversation=conv)

        try:
            raw = call_deepseek(prompt)
        except Exception as e:
            print(f"  ❌ API 调用失败: {e}")
            continue

        parsed = parse_response(raw)
        if not parsed:
            print(f"  ❌ JSON 解析失败，原始输出:")
            print(f"  {raw[:300]}")
            continue

        summary = parsed.get("summary", "")
        facts = parsed.get("facts", [])

        print(f"  summary: {summary}")
        print(f"  facts 数量: {len(facts)}")

        tiers = {"identity": 0, "status": 0, "event": 0, "unknown": 0}
        groups = {1: 0, 2: 0, 3: 0, 4: 0, 0: 0}
        for f in facts:
            tier = f.get("temporal_tier", "unknown")
            grp = f.get("knowledge_group", 0)
            tiers[tier] = tiers.get(tier, 0) + 1
            groups[grp] = groups.get(grp, 0) + 1

        print(f"  temporal_tier: {tiers}")
        print(f"  knowledge_group: {groups}")

        print(f"\n  --- facts 详情 ---")
        for f in facts:
            tier = f.get("temporal_tier", "?")
            grp = f.get("knowledge_group", "?")
            marker = "🚫" if tier == "event" else "✅"
            print(f"  {marker} [G{grp}|{tier}] {f.get('subject','')} {f.get('predicate','')} {f.get('object','')}")

        all_results.append({"label": label, "facts_count": len(facts), "tiers": tiers, "groups": groups, "facts": facts})

    # 汇总
    print(f"\n{'='*50}")
    print("汇总")
    print(f"{'='*50}")
    total = sum(r["facts_count"] for r in all_results)
    ev = sum(r["tiers"].get("event", 0) for r in all_results)
    ident = sum(r["tiers"].get("identity", 0) for r in all_results)
    stat = sum(r["tiers"].get("status", 0) for r in all_results)
    print(f"总 facts: {total}")
    print(f"  identity: {ident}  status: {stat}  event: {ev} (跳过不写库)")
    print(f"改前基线: ~{total + ev} 条 → 改后写入: {total - ev} 条")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="/mnt/c/Users/Administrator/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db")
    args = parser.parse_args()
    run_trial(args.db)
