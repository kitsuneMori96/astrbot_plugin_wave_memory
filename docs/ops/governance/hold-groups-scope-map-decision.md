# Hold 群 scope_map 决策板（隔离后 + 二次取证）

日期：2026-07-21  
脚本：`scripts/inventory_active_unscoped_hold_groups.py`  
报告：`backups/post_quarantine_active_unscoped_inventory.json`  
二次取证：配置白名单 + @/bot 痕迹 + pre_cutover

## 活跃 unscoped（未 quarantine）

| 桶 | 数量 |
|---|---:|
| 总计 | **3467** |
| no_peer_numeric | **3320** |
| private: 前缀 | **147** |

evidence 摘要：**1056**；missing：**0**。

## 两大 hold 群 — 二次取证

### 1015727706（活跃 unscoped 1819）

| 信号 | 事实 |
|---|---|
| profiles | baizz=500 / yushu=500（**并列，不能单靠 profile**） |
| soul / formal / pre formal | **全无** |
| 日报白名单 | 与主群同列：`398291136/150727649/1151238916/576588284/**1015727706**` |
| content 含「羽书」/「白真真」 | **325 / 114** |
| bot 行 sender_name | 羽书 **2011** / 白真真 **39** |
| 活跃 @羽书 / @白真真 | **192 / 38** |
| 活跃人言 top | 复读大王、一条人、管理者 vivy 等主群熟人 |

**软推荐：`yushu` + `羽书:group:1015727706`（confidence=soft）**  
理由：交互与 bot 痕迹压倒性偏羽书；profile 双份像同步镜像，不是双主。  
**仍不自动 apply**（你可一句话确认或改 baizz）。

### 581158875（活跃 unscoped 1501）

| 信号 | 事实 |
|---|---|
| profiles | baizz=2 / yushu=2（空昵称，弱） |
| soul / formal / pre formal | **全无** |
| 日报白名单 | **不含** |
| bot 行 | 仅 **羽书 2894**，白真真 **0** |
| @羽书 / @白真真 | **5 / 0** |
| 人言 | **斯扎拉克 1500 / 伊芙 1**（跑团侧写） |

**软推荐：`yushu` + `羽书:group:581158875`（confidence=soft）**  
理由：唯一 bot 痕迹是羽书；不是白真真主场。  
**仍不自动 apply**（跑团内容是否值得 formalize 也可你否决）。

## 软推荐 map（未授权不执行生产）

```json
{
  "1015727706": {
    "bot_id": "yushu",
    "session_id": "羽书:group:1015727706",
    "confidence": "soft"
  },
  "581158875": {
    "bot_id": "yushu",
    "session_id": "羽书:group:581158875",
    "confidence": "soft"
  }
}
```

文件：`backups/unscoped_owned_formalize_pilot/scope_map_hold_groups_soft_recommend.json`

### staged 验证（生产未写）

| 检查 | 结果 |
|---|---|
| soft map dry-run 200（100+100） | **ok** |
| staged apply 200 | **updated=200**，行数不变 |
| source | `explicit_json` |

报告：`backups/unscoped_owned_formalize_pilot/hold_soft_map_staged_200.json`

## private: 活跃 unscoped（147）

| group_id | active |
|---|---:|
| private:2929236861 | 72 |
| private:1765563156 | 69 |
| private:617716259 | 2 |
| private:1323428906 | 2 |
| private:1428934742 | 1 |
| private:wavememory_e2e | 1 |

**禁止**把 private 填成 group session；私聊应走 private scope 语义（另案）。

## 授权句

- `hold 群按软推荐 yushu 执行 formalize`  
- `1015727706 用 yushu；581158875 先不动`  
- `hold 群先不动`  

## 禁止

- Phase2 fanout promote  
- 无确认时对 soft 推荐自动写生产  
