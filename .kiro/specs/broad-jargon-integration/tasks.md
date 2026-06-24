# Implementation Plan — Broad Jargon Integration (广域黑话集成与前端双栏管理)

## 1. 目标 (Overview)
实现免配置克隆、本地打包常用词条与语料（Holyman）、实时在线 GitHub 接口/代理一键同步并热加载、以及前端双栏（本地统计 vs 广域词库）独立激活与 Prompt 注入的完整集成。

---

## 2. 执行步骤 (Tasks)

### Phase 1: 内置资源加载与基础结构搭建 (Acceptance Criteria: 单元测试无报错)
- [ ] **Task 1.1**：创建内置资源资产目录 `astrbot_plugin_wave_memory/assets/holyman/`
- [ ] **Task 1.2**：编写提取好的初始 fallback 黑话词库 `phrases.json`（内置大约 30 个极其普遍的抽象梗）与语料库示例 `corpus.json` 到上述目录
- [ ] **Task 1.3**：重构并替换已有的 `services/jargon/holyman_reference.py` 模块：
  - 弃用以前手动传入 `root_path` 读本地目录的非高可用设计。
  - 默认加载内置 `assets/holyman/phrases.json` 和 `corpus.json`。
  - 保留 `match` 与 `_find_examples` 对外层接口的兼容性。
  - 新增热重载 API：`reload()`，允许后台在线同步后原地重新读取资源。

### Phase 2: 后端在线同步、双栏 API 实现 (Acceptance Criteria: Python 语法正确，API 可正常响应)
- [ ] **Task 2.1**：实现在线同步服务 `astrbot_plugin_wave_memory/services/jargon/sync.py` 的 `HolymanSyncService`：
  - 请求 `https://raw.githubusercontent.com/ykdeso/holyman-skills/main/%E7%A5%9E%E4%BA%BA.skill/SKILL.md` (或支持国内 proxy `https://mirror.ghproxy.com/` 进行加速)。
  - 请求 `https://raw.githubusercontent.com/ykdeso/holyman-skills/main/%E7%A5%9E%E8%A8%80.txt`。
  - 提取解析其中短语、释义和例句，重写覆写 `assets/holyman/` JSON 资产并热重载 `HolymanReference`。
- [ ] **Task 2.2**：重构并新增 `/api/jargon` Web 路由 `webui/blueprints/jargon.py`：
  - 新增 `GET /api/jargon/holyman`：拉取内置/已同步的 Holyman 静态词条，通过 `LEFT JOIN` 本地 `jargon` 表中的 word 字段确定激活状态并返回。
  - 新增 `POST /api/jargon/holyman/toggle`：接收 word 和 activate，在 `jargon` 库中插入或删除/注销，并把 scope 设为 `global`，source 设为 `holyman_skills`。
  - 新增 `POST /api/jargon/holyman/sync`：触发后台同步并返回结果。

### Phase 3: WebUI 前端重构 (Acceptance Criteria: 页面可无缝加载无报错，子 Tab 操作正常)
- [ ] **Task 3.1**：修改 `webui/static/index.html` 对应 `tab === 'jargon'` 区域：
  - 新增二级标签子页切换：群聊本地黑话 (Local) 与 广域抽象黑话 (Global)。
  - 对于广域黑话子页，引入“在线一键同步”操作台（附国内加速代理开关），和黑话词条全局 Prompt 激活 Swtich 开关列表。
- [ ] **Task 3.2**：在 `webui/static/app.js` 中补齐 `loadJargonHolyman()`、`toggleJargonHolyman()` 和 `syncJargonHolyman()` 数据绑定函数。

### Phase 4: 整合运行与闭环验证 (Acceptance Criteria: 宿主可重启无报错，前端双栏工作正常，日志输出成功)
- [ ] **Task 4.1**：进行后端语法检查：`python -c "import ast; ast.parse(open('webui/blueprints/jargon.py', encoding='utf-8').read())"`等。
- [ ] **Task 4.2**：将修改过的代码同步至运行时目录，重启宿主，扫描运行日志。
- [ ] **Task 4.3**：在 Web 界面测试一键同步和手动激活功能，确认数据库持久状态，宣布闭环完成。
