# Broad Jargon Integration Requirements — 广域黑话集成与前端双栏管理

## 1. 目标 (Objectives)

1.1 **免配置开箱即用**：将 `ykdeso/holyman-skills` 最新抽象文化语料和词条内置于插件的 assets 中，使用户无需手动克隆或配置 `holyman_path` 即可天然使用广域黑话理解。
1.2 **在线热更新**：支持前端一键/后台在线从 GitHub (或可用代理镜像如 ghproxy.com) 拉取并同步最新广域黑话，原地热更新内置黑话资源。
1.3 **前端精细化双栏管理**：
    - 设计二级子标签页：“群聊本地黑话” 与 “广域抽象黑话（Holyman）”。
    - 本地黑话：负责查看并审核通过聊天自动挖掘的词条。
    - 广域黑话：支持全量展示内置 Holyman 词库，并提供“一键/单独激活为全局黑话”、“在线同步”机制。
1.4 **安全注入与 Token 防爆**：
    - **理解（Match）层**：只要在内置/已同步词库中，不管是否激活，羽书都保持静默看懂（不注入 Prompt 避免炸 Token，但在模型理解链路中作为备忘参考）。
    - **注入（Inject）层**：只有手动点击了“启用/激活”或者在群内聊天命中触发了自动挖掘、并处于 `confirmed` + `is_global = 1` 状态的词条，才会执行 Prompt 注入，保证绝对的安全可控。

## 2. 场景约束 (User Scenarios)

2.1 **首次部署**：
    - 插件启动，检测 `assets/holyman/` 目录。如果不存在，则使用内置 Fallback 初始化。
    - WebUI 概览或配置项不再强制要求输入 `holyman_path`。
2.2 **用户打开 WebUI 黑话管理 Tab**：
    - 二级 Tab1: **群聊本地黑话**
        - 列出 `scope = 'local'`（或 `source = 'wave_memory'`）的词条。
        - 展现群号、频次、当前状态、推断释义、审核/拒绝/编辑。
    - 二级 Tab2: **广域抽象黑话 (Holyman)**
        - 异步加载当前内置的 Holyman 静态词库列表（合并数据库中已激活的记录）。
        - 列表字段：词条、内置释义、状态（已激活/静默理解中）。
        - 动作：提供一个一键 **"🔄 在线同步最新"** 的按钮。
        - 动作：每一个词条拥有一个 Switch 开关（激活/取消激活）。激活后直接把该词落库并设定 `is_global = 1`, `status = 'confirmed'`, `source = 'holyman_skills'`；取消激活时，将其从 `jargon` 库中状态标记为 `pending`。
2.3 **后台执行同步**：
    - 点击在线同步后，后台异步请求 `https://raw.githubusercontent.com/ykdeso/holyman-skills/main/神人.skill/SKILL.md` (或镜像源) 和 `神言.txt`。
    - 同步完成后，重写内置资源文件，重新热加载 `HolymanReference` 内存缓存，并在前端给用户弹出“同步完成（共加载 XXX 条黑话，XXX 条语料）”提示。
