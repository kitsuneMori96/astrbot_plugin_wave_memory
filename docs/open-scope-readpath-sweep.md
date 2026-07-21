# 读路径 bot+session 残留扫除（不写库 / 不 fanout）

日期：2026-07-21

## Protected

禁 destructive/fanout 仍 **blocked**。本回合只改读代码 + 重启加载。

## 已确认 / 已修

| 路径 | 状态 |
|---|---|
| FTS5 / Timeline / memory_repo get_by_ids / cold | 已 group-open |
| **deep_search** | 已 group-open；容器已加载；重启后 Fully initialized |
| **WebUI memories 列表** | 已 group-open（原 bot+session+resolved 会在 qq:session 下空列表） |

## 生产对照（主群「是谁」）

- open(group_id)：**14**
- strict(qq:group session)：**0**

## 仍保持 bot+session 的路径（有意）

- **写路径**：message_writer / memory_mutations / feedback 更新  
- **Soul/关系/知识正式表**：scoped_soul / scoped_knowledge 等  
- **WebUI 管理写接口**  

这些不是「聊天检索历史 0 命中」主因；批量改 session 编码需另授权。

## 启动

- Init memories **369810**（后台 eviction 可能略减行，非本回合手动清理）  
- Fully initialized  
