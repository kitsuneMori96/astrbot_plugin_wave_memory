# WaveMemory 拟真心智进化：吸收三大高星插件核心机制的深度注入计划

本计划旨在吸收 `private_companion`（拟人陪伴）、`self_learning`（自主学习）和 `screen_companion`（屏幕伙伴）三大顶级插件在**时空感知、记忆时间冷淡、潜意识犹豫与消息合并防抖**上的核心优势，并将其与 WaveMemory 已有的“灵魂引擎 + BDI 心智 + 向量知识图谱”进行深度搭配，通过 `inject_memory` 进行高性能、全天候的有机注入。

---

## 🎯 一、 核心模块设计

### 模块 1：多维关系半衰遗忘与“衰减信念”注入（学习自 `self_learning` / `MaiBot`）
* **机制（物理计算）**：
  * 修改 `services/lifecycle.py` 里的 `_run_decay` 方法，在每日定时任务中，引入对 `user_profiles` 表中所有建立过情感连接用户的多维情感度（`trust` / `familiarity` / `fun` / `depth`）的**二次函数半衰期衰减**：
    * 设定 15 天为全衰减周期（`decay_days=15`）。
    * 每天计算用户距离上次互动的间隔天数。衰减因子计算公式：
      $$decay\_factor = \min(0.01, \frac{0.01}{15^2} \times days^2)$$
    * 对熟悉度、信任度等维度进行乘性+线性递减。当跌破临界点时，自动将用户的态度等级（`attitude_level`）降级（如亲密降为普通，熟悉降为冷淡）。
* **搭配（信念注入）**：
  * **记忆痕迹的消逝感**：在 `inject_memory` 的信念检索中，如果检测到用户的关系因为时间遗忘发生了降级（`decay_downgrade_noted`），在 `belief_text` 顶部自动注入：
    `- (置信度 95%): (来自关系衰减) 你们已经很长一段时间没有深入聊天了。你虽然记得以前和他很亲密（曾达到 {old_attitude} 态度），但内心的生疏与隔阂无法忽视。你在交谈中会不自觉地收起以前的亲昵，语气变得更有礼貌、也更有边界感。`
  * 这在不破坏底座人格的同时，让羽书展现出极其生动的“久别生疏感”。

### 模块 2：主动插话咽回与“犹豫心里话”注入（学习自 `private_companion`）
* **机制（物理捕获）**：
  * 在 `user_profiles.metadata` 中开辟 `recent_hesitations`（最近产生但被截断/没发出去的插话念头列表，保留最近 5 条）。
  * **咽回与抢词拦截**：在 `main.py` 的 `feed_message` 时，检测 `MetaThinking` 当前是否对该群组持有正在等待/处于生成途中的主动插话计划。如果有，且用户此时突然说话，则判定为“用户抢词打断”：
    * 自动拦截并撤销当前的主动插话任务（让位给抢词）。
    * 将该念头（Topic/Motive）以及时间戳压入 `recent_hesitations` 暂存。
* **搭配（灵魂注入）**：
  * **心理解冻与潜意识残留**：当大模型被动唤醒回复用户的信息时，在 `inject_memory` 的 `_ch_soul` 通道最底部，如果检测到 30 分钟内有刚咽回去的犹豫：
    * 将刚才咽回去的“心愿与犹豫”用自然语言包装，作为 **`[咽回去的潜意识念头 (Unspoken Desire)]`** 追加进 `persona_text`。
    * 提供语气指导：`“你在回复时，可以通过：‘其实我刚才就想问你来着，后来见你在聊别的就先没说，你刚才...’ 的转折，自然地将话题绕回或者顺带重提，展现心理解冻的灵动感。”`

### 模块 3：全天候生理生物钟（学习自 `screen_companion` 与生物钟心境）
* **机制与搭配（灵魂注入）**：
  * 在 `inject_memory` 里的 `_ch_soul`（灵魂通道）中，根据当前的真实小时（Hour 0-23）计算并注入 **`【生理生物钟（Circadian Soul State）】`**，作为最底层的心境背景：
    * **凌晨时分 (0:00 - 5:00)**：注入：`极度困倦（精力 15%）；注意力极其涣散；社交兴致几乎不想说话。本能地精简字数（不再长篇大论），情绪自动归于平静（CALM），更倾向于互道晚安。`
    * **清晨时分 (6:00 - 8:00)**：注入：`晨曦刚醒（精力 40%）；大脑缓慢恢复中。语气温和、慵懒、平静，会不自觉地表达刚睡醒的状态。`
    * **工作日下午 (9:00-11:30, 14:00-17:00)**：注入：`精力充沛（精力 95%）；注意力高度集中；对万物充满好奇和干劲。`
    * **深夜感性 (22:00 - 23:59)**：注入：`夜色感性（精力 45%）；注意力容易发散、多愁善感。依恋度拉高，非常适合深度情感交心。`
  * 这强力纠偏了大模型在深夜秒回、长篇大论的“机械感”，实现生物钟对情绪和语气的动态调和。

### 模块 4：消息合并防抖收口（4秒滑动窗口物理并发熔断）
* **机制（消息防抖）**：
  * 模仿 `private_companion`，在 `main.py` 的 `on_message` 中加入物理级和语义级 4.0 秒的滚动延迟缓冲（`_semantic_message_buffers`）。
  * 当用户在群聊/私聊中快速连续发短句、或是图文异步错开发送时，系统会自动将 4.0 秒内同一来源的消息、图片、媒体对象打包。
  * **滑动延长**：4.0 秒内只要有新发送，截止时间顺延，最长 12 秒强制合并。合并后，带上发送者昵称整合成一轮大模型请求，彻底解决刷屏、消息分裂和 Token 炸裂问题。

---

## 🤖 二、 Subagents 并行派遣执行方案

为了极致的速度与质量，我们将任务解耦为三个核心方向，批准后派遣三个 specialized subagents 并行开发：

### Subagent A：关系衰减与消息防抖收口 (Decay & Debounce)
* **职责**：
  * 重构 `services/lifecycle.py` 里的 `_run_decay`：实现多维情感值的 15 天二次插值半衰衰减，并在发生态度降级时在 metadata 中置入 `decay_downgrade_noted`。
  * 修改 `main.py` 的 `on_message` 入口，写出基于 4s 缓冲池的 `message_debounce` 消息合并防抖，完美聚合多段文本与图片。

### Subagent B：抢词截断与犹豫解冻注入 (Hesitation & Unfreeze)
* **职责**：
  * 在 `main.py` 的 `feed_message` 中加入对 `MetaThinking` 活跃计划的抢词和撤回检测，将咽回的主动插话记录为 metadata 中的 `recent_hesitations`。
  * 在 `main.py` 的 `_ch_soul` 通道中，读取 30 分钟内的犹豫记忆，作为带有“心理解冻”提示词的 `Unspoken Desire` 注入灵魂深处。

### Subagent C：全天候生理生物钟注入与脱敏 (Circadian & Masking)
* **职责**：
  * 在 `main.py` 的 `_ch_soul` 通道中写出基于 24 小时制的 `Circadian Soul State` 动态提示词注入。
  * 对 memories 进行检索时的路径和敏感词截断。

---

## 🧪 三、 验证与质量检查

1. **语法检查**：
   * 对修改后的所有 Python 文件（`main.py`, `services/lifecycle.py`）运行 `python -m py_compile` 静态编译检查。
2. **端到端重启**：
   * 使用 `docker cp` 往容器内绝对路径同步，并重启容器：`docker restart astrbot`。
3. **日志回归监控**：
   * 观察 `docker logs` 日志输出，确认无 `traceback` 或挂起，且在 `inject_memory` 时：
     * 日志成功打印带有 `[WaveMemory] inject_memory SUCCESS bot=yushu: [circadian, hesitation, decay_belief]` 标志。
