# Broad Jargon Integration Design — 广域黑话集成与前端双栏管理设计

## 1. 资源目录与打包结构 (Resource Asset Structure)

在 `astrbot_plugin_wave_memory` 根目录下新增静态资源资产：

```text
astrbot_plugin_wave_memory/
  └── assets/
      └── holyman/
          ├── phrases.json     # 经过结构化解析提取的 { "词条": "内置释义" }
          └── corpus.json      # 提取的示例语料数组 [ "语料句1", "语料句2" ]
```

### 1.1 `phrases.json` 示例
```json
{
  "v我50": "常见抽象文案结尾：用突然要钱制造荒诞转折。",
  "你说得对但是": "常见反串起手式：表面承认，随后切入夸张传教或长文。"
}
```

### 1.2 `corpus.json` 示例
```json
[
  "深情铺垫了这么多，最后来一句v我50",
  "你说得对，但是《原神》是由米哈游自主研发的一款全新开放世界冒险游戏"
]
```

---

## 2. 在线同步服务 (Online Sync Service)

在 `services/jargon/sync.py` 中建立 `HolymanSyncService`：

### 2.1 核心 API 接口
`await sync_mgr.sync_from_github(proxy_url: str | None = None) -> dict`
- **源 URL**：
  - phrases: `https://raw.githubusercontent.com/ykdeso/holyman-skills/main/%E7%A5%9E%E4%BA%BA.skill/SKILL.md` (国内备用 `https://mirror.ghproxy.com/` 代理)
  - corpus: `https://raw.githubusercontent.com/ykdeso/holyman-skills/main/%E7%A5%9E%E8%A8%80.txt`
- **解析逻辑**：
  - 读取 `SKILL.md` 的 Markdown 列表，正则匹配 `- 词条` 或 `* 词条` 提取短语。
  - 读取 `神言.txt`（它目前是一个 JSON 文件包含 items 数组，或纯文本行），提取 text 作为 corpus。
  - 写入 `assets/holyman/` 本地 JSON，并在同步成功后，直接调用 `jargon_service._holyman.reload()` 热重载内存缓存。

---

## 3. WebUI API 接口设计 (Quart Endpoints)

修改 `webui/blueprints/jargon.py`：

### 3.1 获取所有 Holyman 词库与状态
`GET /api/jargon/holyman`
- **功能**：将内置 JSON 中的 phrases 数据全部读取出来，并与本地 `jargon` 数据库做 `LEFT JOIN`（通过 word 匹配）。
- **返回数据结构**：
  ```json
  {
    "items": [
      {
        "word": "v我50",
        "meaning": "常见抽象文案结尾：...",
        "is_activated": true,     // 是否已在 jargon 表中 confirmed
        "db_id": 12,              // 若已入库，则返回主键 id，用于切换
        "source": "holyman_skills"
      }
    ]
  }
  ```

### 3.2 切换激活状态
`POST /api/jargon/holyman/toggle`
- **Payload**：`{ "word": "v我50", "meaning": "...", "activate": true }`
- **逻辑**：
  - `activate = true`：如果不存在，则往 `jargon` 表插入一条记录：
    `INSERT INTO jargon (word, meaning, is_jargon, is_global, group_id, status, scope, source) VALUES (?, ?, 1, 1, 'global_fallback', 'confirmed', 'global', 'holyman_skills')`
  - `activate = false`：更新 `jargon` 表，标记 `status = 'pending'` 或者直接将 `is_jargon = 0` / 删除此行，从 Prompt 注入候选列表中移除。

### 3.3 触发在线同步
`POST /api/jargon/holyman/sync`
- **功能**：调用 `HolymanSyncService` 异步开始拉取 GitHub，原地覆写本地资产并热重载。

---

## 4. 前端双标签页设计 (WebUI Frontend)

修改 `webui/static/index.html` 的黑话 Tab 和 `webui/static/app.js`：

### 4.1 二级子 Tab
```html
<div class="flex border-b border-gray-700 mb-3 gap-4 text-xs">
    <button @click="jargonSubTab='local'" :class="jargonSubTab==='local' ? 'border-b-2 border-primary font-medium text-white pb-2' : 'text-muted-fg pb-2'">群聊本地黑话</button>
    <button @click="jargonSubTab='global'" :class="jargonSubTab==='global' ? 'border-b-2 border-primary font-medium text-white pb-2' : 'text-muted-fg pb-2'">广域抽象黑话 (Holyman)</button>
</div>
```

### 4.2 广域黑话界面组件
- 顶部放置 **"🔄 在线同步最新"** 和 一个 `useProxy` 开关。
- 列表包含词条、内置释义、双击编辑，以及一个 **"Prompt 注入"** 开关。
- 默认提供按关键词全文过滤搜索的 `Input` 框。
