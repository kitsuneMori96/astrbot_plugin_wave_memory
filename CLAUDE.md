# Wave Memory Plugin — 开发规则

## 版本号与发版

当前版本：**v1.0.1**

### 版本号规则（SemVer）

```
v{major}.{minor}.{patch}
```

- **major**：破坏性变更（API 不兼容、数据库 schema 不兼容迁移）
- **minor**：新功能（向后兼容）
- **patch**：bug 修复、性能优化

### ⚠️ 每次 commit 的规则（强制）

1. **改代码的同时更新 CHANGELOG.md** — 不能"先 commit 后补 changelog"
2. **commit message 格式**：`feat/fix/docs/perf/style(scope): 简述`
3. **不要在 commit 后才发现遗漏** — commit 前检查 `git diff --stat`

### ⚠️ 发版检查清单（必须按顺序执行）

```bash
# 1. 查看自上个 tag 以来所有 commit（确认没遗漏）
git log $(git describe --tags --abbrev=0)..HEAD --oneline

# 2. 确认 CHANGELOG.md 包含以上全部 commit 的变更描述
cat CHANGELOG.md | head -40

# 3. 确认 metadata.yaml version 已更新
grep version metadata.yaml

# 4. 语法检查
python -c "import ast; ast.parse(open('main.py',encoding='utf-8').read())"

# 5. 提交版本 bump（如果 CHANGELOG/metadata 还没 commit）
git add CHANGELOG.md metadata.yaml
git commit -m "release: vX.Y.Z"

# 6. 打 tag
git tag vX.Y.Z

# 7. 推送（代码 + tag 一起）
git push origin master --tags

# 8. 创建 GitHub Release（body = CHANGELOG 对应段落）
gh release create vX.Y.Z --title "vX.Y.Z — 简述" --notes-file -
```

### 何时发版

| 场景 | 动作 |
|------|------|
| 完成一个功能阶段 | minor 版本 |
| 修了 bug / 小优化 | patch 版本 |
| 日常开发中间态 | 直接 push，不打 tag |
| 需要 AstrBot 插件市场更新 | 必须 tag + release |

### 历史教训

- **2026-05-29**：插件被 GitHub 回退覆盖，丢失本地代码。教训：本地必须 push，不能只存运行时。
- **2026-06-14**：v1.0.1 发版时 release notes 遗漏了 5 个 commit。教训：发版前必须 `git log tag..HEAD` 检查全部变更。

---

## 项目结构

```
├── main.py              # 插件入口
├── metadata.yaml        # AstrBot 插件元数据
├── CHANGELOG.md         # 版本变更记录
├── engine/              # 核心算法（EPA、共现、残差、金字塔）
├── services/            # 业务服务（生命周期、整合、Tag提取、审计）
├── webui/               # WebUI 服务 + 静态页面
│   ├── __init__.py      # FastAPI 路由
│   └── static/          # HTML 页面（explore/maintain）
└── tools/               # AstrBot function tools
```

## 开发约束

- Python 3.10+，依赖随 AstrBot 环境
- SQLite 单文件数据库，schema 变更用 `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE` 兼容
- WebUI 独立端口 9876，不依赖 AstrBot Pages
- LLM 调用通过 `self.context.get_using_provider()` 获取 provider
- Embedding 通过 `self.context.get_using_provider()` 获取，不可用时 graceful fallback
