# WaveMemory v0.8 Soul Engine — 技术设计

## 整体架构

```
┌─────────────────────────────────────────────────┐
│                   灵魂层 (Soul)                   │
│                                                   │
│  BeliefSystem    ConcernTracker    DesireEngine  │
│  MoodTrajectory  SubjectiveTime                  │
│                                                   │
├──────────────────── 决策层 ────────────────────────┤
│                                                   │
│  MetaThinking v2（综合灵魂层 → 输出行动）          │
│  主动行为分层: ignore / react / lite / full       │
│                                                   │
├──────────────────── 记忆层 ────────────────────────┤
│                                                   │
│  Source 分层: core | chat | noise | evolution |   │
│              experience | lore | belief           │
│                                                   │
│  写入门控 → HNSW热索引 + DB冷存储 → 淘汰回收     │
│  检索: 向量 + BM25 → RRF 融合 → Spike重排        │
│                                                   │
├──────────────────── 基础设施 ──────────────────────┤
│                                                   │
│  SQLite (wave_memory.db)                          │
│  HNSW (hnswlib)                                   │
│  EmbeddingService                                 │
│  LLMFallbackClient                                │
│                                                   │
└─────────────────────────────────────────────────┘
```

---

## Phase 1: 记忆分层

### 数据结构变更

memories 表新增字段（ALTER TABLE）：
- `source` 已存在（live/bzz_experience/book_lore/bzz_evolution）
- 将 `live` 拆分为 `core` / `chat` / `noise`

<!-- PLACEHOLDER_DESIGN_P1 -->

### 写入门控（MessageWriter 级别，零 LLM 调用）

```python
def classify_source(message: str, sender_id: str, bot_profile: BotProfile, event) -> str:
    """规则引擎，在写入时判断 source 分类。"""
    # 1. bot 自己发的 → core
    if sender_id == "bot":
        return "core"
    # 2. 消息含 @bot → core
    if is_at_bot(event):
        return "core"
    # 3. 消息含 bot 名字/别名 → core
    if any(name in message for name in bot_profile.all_keywords):
        return "core"
    # 4. 消息 < min_length(默认10字) → noise
    if len(message.strip()) < 10:
        return "noise"
    # 5. 其余 → chat
    return "chat"
```

### TagWorker 升级判断

TagWorker 打标签后执行二次检查：
```python
def maybe_upgrade_source(memory_id, tags, bot_keywords):
    """如果标签中包含 bot 相关词，升级 chat → core。"""
    tag_names = {t["name"] for t in tags}
    if tag_names & bot_keywords:
        db.update_source(memory_id, "core")
        memory_index.ensure_loaded(memory_id)  # 确保加入热索引
```

### 索引策略

| source | 入 HNSW 热索引 | 存 DB | 淘汰 |
|--------|--------------|-------|------|
| core | ✅ 始终 | ✅ | 永不 |
| chat | ✅ 初始入 | ✅ | 30天无访问 → 移出索引 |
| noise | ❌ | ✅ | 7天后删除 |
| evolution | ✅ 始终 | ✅ | 永不 |
| experience | ✅ 始终 | ✅ | 永不 |
| lore | ✅ 始终 | ✅ | 永不 |
| belief | ✅ 始终 | ✅ | archived后移出索引 |

### 淘汰任务（EvictionService）

```python
class EvictionService:
    """定期淘汰低价值记忆。"""
    
    async def evict_cycle(self):
        # 1. noise: 删除 7 天前的
        self.db.delete_memories(source="noise", older_than=7*86400)
        
        # 2. chat: 30 天未访问的移出索引
        stale_ids = self.db.get_stale_memories(
            source="chat", 
            last_accessed_before=30*86400
        )
        for mem_id in stale_ids:
            self.memory_index.remove(mem_id)
            self.db.mark_evicted(mem_id)  # 标记为已移出索引，DB保留
```

### 查询路由

```python
# QueryEngine.query() 改造
DEFAULT_SOURCES = ["core", "evolution", "experience", "lore", "belief"]
FULL_SOURCES = None  # None = 搜全部

async def query(self, text, ..., source_filter=None):
    if source_filter is None:
        source_filter = DEFAULT_SOURCES  # 默认只搜高价值
    ...
```

### 存量数据迁移

对已有 128k 条 `source=live` 记忆的迁移：
```sql
-- noise: < 10字
UPDATE memories SET source='noise' WHERE source='live' AND LENGTH(content) < 10;
-- core: 含 bot 回复
UPDATE memories SET source='core' WHERE source='live' AND sender_id='bot';
-- 剩余 live → chat
UPDATE memories SET source='chat' WHERE source='live';
```

迁移后重建 HNSW（移除 noise，保留 core+chat+其他）。

---

## Phase 2: BeliefEngine

### 数据结构

新表 `beliefs`:
```sql
CREATE TABLE beliefs (
    id INTEGER PRIMARY KEY,
    content TEXT NOT NULL,           -- "斯扎拉克说话绕但没恶意"
    type TEXT NOT NULL,              -- person_judgment / world_view / self_identity / preference
    strength REAL DEFAULT 0.5,      -- 0-1
    bot_id TEXT NOT NULL,            -- 哪个bot的信念
    sources TEXT DEFAULT '[]',      -- JSON: 支撑记忆ID列表
    conflicts TEXT DEFAULT '[]',    -- JSON: 矛盾信念ID列表
    status TEXT DEFAULT 'active',   -- active / challenged / archived
    created_at REAL,
    last_reinforced REAL,
    archived_reason TEXT             -- 被推翻的原因
);
```

### 信念生成（集成到 ConsolidationService）

```python
class BeliefExtractor:
    """从 consolidation 摘要中提取信念。"""
    
    EXTRACT_PROMPT = """分析以下记忆摘要，提取 0-2 条稳定判断（如果有的话）。
    
    稳定判断 = 反复出现的模式、对某人/某事的一致性看法、或对自己的认知。
    不是事实陈述，是主观判断。
    
    记忆摘要：
    {summary}
    
    已有信念（避免重复）：
    {existing_beliefs}
    
    输出格式（JSON数组，没有就返回[]）：
    [{"content": "...", "type": "person_judgment|world_view|self_identity|preference"}]
    """
    
    async def extract(self, summary: str, existing: list[dict]) -> list[dict]:
        ...
```

### 信念强化/动摇

在 MetaThinking 每次判断后：
```python
async def update_beliefs(self, message, sender_id, bot_id, result):
    """检查当前交互是否影响已有信念。"""
    # 搜索相关信念
    related = self.belief_db.search_by_person(sender_id) + \
              self.belief_db.search_by_topic(message)
    
    for belief in related:
        # 简单规则：如果行动与信念一致 → 强化
        # 如果 inner_thought 中出现矛盾 → 动摇
        if result.get("belief_challenge"):
            belief.strength -= 0.1
            if belief.strength < 0.2:
                belief.status = "challenged"
        else:
            belief.strength = min(1.0, belief.strength + 0.05)
            belief.last_reinforced = time.time()
```

### 信念注入

```python
def get_belief_injection(self, sender_id: str, topic_keywords: list[str]) -> str:
    """获取与当前对话相关的信念注入文本。"""
    beliefs = []
    # 1. 对这个人的判断
    beliefs += self.db.get_beliefs(type="person_judgment", about=sender_id)
    # 2. 与话题相关的世界观
    beliefs += self.db.search_beliefs_by_keywords(topic_keywords)
    # 3. 自我认知（始终注入）
    beliefs += self.db.get_beliefs(type="self_identity", bot_id=self.bot_id)
    
    if not beliefs:
        return ""
    
    lines = ["<beliefs>"]
    for b in beliefs[:5]:  # 最多5条
        lines.append(f"- [{b.type}] {b.content} (确信度:{b.strength:.0%})")
    lines.append("</beliefs>")
    return "\n".join(lines)
```

---

## Phase 3: ConcernTracker

### 数据结构

内存中维护，定期持久化到 DB：
```python
@dataclass
class Concern:
    topic: str              # "斯扎拉克跑团被抓"
    intensity: float        # 0-1，衰减
    origin_memory_id: int   # 触发来源
    bot_id: str
    created_at: float
    last_triggered: float
    decay_rate: float = 0.9  # 每小时乘以此系数
```

新表 `concerns`:
```sql
CREATE TABLE concerns (
    id INTEGER PRIMARY KEY,
    topic TEXT NOT NULL,
    intensity REAL,
    bot_id TEXT,
    origin_memory_id INTEGER,
    created_at REAL,
    last_triggered REAL
);
```

### 生成与衰减

```python
class ConcernTracker:
    def __init__(self, db, bot_id, max_concerns=10):
        self.concerns: list[Concern] = []
        self.max_concerns = max_concerns
    
    def add_concern(self, topic: str, origin_id: int, intensity: float = 0.7):
        """新增关切。如果已有相似主题，强化而非新增。"""
        for c in self.concerns:
            if self._is_similar(c.topic, topic):
                c.intensity = min(1.0, c.intensity + 0.3)
                c.last_triggered = time.time()
                return
        
        if len(self.concerns) >= self.max_concerns:
            # 淘汰最弱的
            self.concerns.sort(key=lambda c: c.intensity)
            self.concerns.pop(0)
        
        self.concerns.append(Concern(topic=topic, intensity=intensity, ...))
    
    def tick(self):
        """衰减 + 清理。每小时调用。"""
        for c in self.concerns:
            hours_elapsed = (time.time() - c.last_triggered) / 3600
            c.intensity *= c.decay_rate ** hours_elapsed
        
        self.concerns = [c for c in self.concerns if c.intensity > 0.1]
    
    def match(self, message: str) -> float:
        """返回消息与当前关切的匹配度（0-1）。"""
        max_score = 0
        for c in self.concerns:
            if any(word in message for word in c.topic.split()):
                max_score = max(max_score, c.intensity)
        return max_score
```

---

## Phase 4: MoodTrajectory

### 数据结构

```python
@dataclass
class MoodSnapshot:
    timestamp: float
    valence: float      # -1(极差) ~ +1(极好)
    arousal: float      # 0(平静) ~ 1(激动)
    cause: str          # "和斯扎拉克吵了一架"
    bot_id: str
```

新表 `mood_snapshots`:
```sql
CREATE TABLE mood_snapshots (
    id INTEGER PRIMARY KEY,
    bot_id TEXT,
    timestamp REAL,
    valence REAL,
    arousal REAL,
    cause TEXT
);
```

### 轨迹管理

```python
class MoodTrajectory:
    def __init__(self, db, bot_id, window_size=20):
        self.snapshots: deque = deque(maxlen=window_size)
        self.bot_id = bot_id
    
    def record(self, valence: float, arousal: float, cause: str):
        self.snapshots.append(MoodSnapshot(...))
        # 持久化
        self.db.insert_mood_snapshot(...)
    
    @property
    def recent_mood(self) -> str:
        """生成最近情绪摘要，用于注入 context。"""
        if not self.snapshots:
            return ""
        
        avg_valence = sum(s.valence for s in self.snapshots) / len(self.snapshots)
        recent_3 = list(self.snapshots)[-3:]
        
        if avg_valence > 0.3:
            mood_word = "心情不错"
        elif avg_valence < -0.3:
            mood_word = "心情不太好"
        else:
            mood_word = "平平淡淡"
        
        causes = [s.cause for s in recent_3 if s.cause]
        cause_text = f"（{'、'.join(causes[-2:])}）" if causes else ""
        
        return f"[近期状态] {mood_word}{cause_text}"
```

---

## Phase 5: SubjectiveTime

### 时间锚点

```python
@dataclass
class TimeAnchor:
    event_summary: str    # "和斯扎拉克大吵一架"
    timestamp: float
    emotional_weight: float  # 越高越"近"
    bot_id: str
```

### 主观时间描述

```python
class SubjectiveTime:
    def describe_interval(self, target_timestamp: float) -> str:
        """将绝对时间差转为主观描述。"""
        elapsed = time.time() - target_timestamp
        
        # 查找最近的锚点
        nearest_anchor = self._find_nearest_anchor(target_timestamp)
        
        if nearest_anchor and elapsed < 7 * 86400:
            return f"{nearest_anchor.event_summary}之后"
        
        if elapsed < 3600:
            return "刚才"
        elif elapsed < 86400:
            return "今天早些时候"
        elif elapsed < 3 * 86400:
            return "前两天"
        elif elapsed < 7 * 86400:
            return "这周"
        elif elapsed < 30 * 86400:
            return "上个月"
        else:
            return "很久以前"
```

---

## Phase 6: DesireEngine

### 数据结构

```python
@dataclass
class Desire:
    type: str           # "抢红包" / "想聊天" / "想安静" / "想炫耀"
    trigger: str        # 触发事件描述
    intensity: float    # 0-1
    action: str         # 满足欲望需要的行动
    conflict_belief: Optional[str]  # 与哪条信念冲突
    resolution: str     # 博弈结果: "yield"(屈服欲望) / "suppress"(压制) / "compromise"(妥协)
```

### 冲突解决

```python
class DesireEngine:
    def process_desire(self, desire: Desire, beliefs: list[Belief]) -> dict:
        """欲望与信念博弈。"""
        # 找冲突信念
        conflicting = [b for b in beliefs if self._conflicts(desire, b)]
        
        if not conflicting:
            return {"action": desire.action, "resolution": "yield"}
        
        # 博弈：desire.intensity vs max(belief.strength)
        max_belief_strength = max(b.strength for b in conflicting)
        
        if desire.intensity > max_belief_strength + 0.2:
            # 欲望压过信念
            return {"action": desire.action, "resolution": "yield", 
                    "inner": f"算了……{conflicting[0].content}，但我还是想{desire.type}"}
        elif max_belief_strength > desire.intensity + 0.2:
            # 信念压制欲望
            return {"action": "suppress", "resolution": "suppress",
                    "inner": f"想{desire.type}……但{conflicting[0].content}"}
        else:
            # 势均力敌 → 妥协（嘴硬心软）
            return {"action": desire.action + "_reluctant", "resolution": "compromise",
                    "inner": f"才不是因为想{desire.type}，只是……顺手而已"}
```

---

## Phase 7: MetaThinking v2

### 输出扩展

```python
# 完整输出（高强度交互时）
MetaThinkingResult = {
    "action": str,          # reply / ignore / short_reply / attack_back / 主动插话
    "tone": str,            # 正常 / 热情 / 冷淡 / 讽刺 / 犹豫
    "inner_thought": str,   # 内心（已有）
    "concern_update": str,  # "关注:斯扎拉克被抓" / "不变"（新）
    "belief_challenge": bool,  # 这件事动摇了信念吗（新）
    "mood_impact": float,   # -1~1 对情绪影响（新）
    "desire_triggered": str,  # 触发了什么欲望（新，可选）
}
```

### 主动行为分层

```python
class ProactivePolicy:
    """主动行为分层决策。"""
    
    def decide(self, concern_match: float, mood: float, affection: float, topic_relevance: float) -> str:
        score = (
            concern_match * 0.4 +
            topic_relevance * 0.3 +
            affection * 0.15 +
            (mood + 1) / 2 * 0.15  # normalize mood to 0-1
        )
        
        if score < 0.15:
            return "ignore"
        elif score < 0.25:
            return "react"       # 表情回应
        elif score < 0.40:
            return "text_lite"   # 简短一句
        else:
            return "full"        # 完整参与
```

---

## Phase 8: BM25 混合检索

### 实现选择

使用 `rank_bm25` 库 + jieba 分词（或 CJK bigram fallback）。

```python
class HybridRetriever:
    """向量 + BM25 混合检索。"""
    
    def __init__(self, memory_index, bm25_index):
        self.vector = memory_index
        self.bm25 = bm25_index
    
    async def search(self, query_text: str, query_vec, top_k: int = 10) -> list:
        # 1. 向量检索 top_k*2
        vec_results = self.vector.search(query_vec, k=top_k * 2)
        
        # 2. BM25 检索 top_k*2
        bm25_results = self.bm25.search(query_text, k=top_k * 2)
        
        # 3. RRF 融合
        return self._rrf_fuse(vec_results, bm25_results, k=60, top_k=top_k)
    
    def _rrf_fuse(self, vec_results, bm25_results, k=60, top_k=10):
        """Reciprocal Rank Fusion."""
        scores = defaultdict(float)
        for rank, (mem_id, _) in enumerate(vec_results):
            scores[mem_id] += 1.0 / (k + rank + 1)
        for rank, (mem_id, _) in enumerate(bm25_results):
            scores[mem_id] += 1.0 / (k + rank + 1)
        
        sorted_ids = sorted(scores, key=scores.get, reverse=True)
        return sorted_ids[:top_k]
```

### BM25 索引维护

- 写入时同步更新 BM25 索引（仅 core + chat）
- noise 不入 BM25
- 定期全量重建（启动时）
