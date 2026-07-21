# private: 前缀 unscoped 策略（只读）

日期：2026-07-21  
报告：`backups/private_unscoped_strategy_readonly.json`

## 规模

| 项 | 值 |
|---|---:|
| 活跃 private unscoped | **147** |
| 已有 formal private peer | **0** |

| group_id | active |
|---|---:|
| private:2929236861 | 72 |
| private:1765563156 | 69 |
| private:617716259 | 2 |
| private:1323428906 | 2 |
| private:1428934742 | 1 |
| private:wavememory_e2e | 1 |

## 策略

1. **禁止**映射为 `*:group:*`  
2. formalize 若做，session 应为 `平台:private:<uid>` 一类私聊编码  
3. bot 选择：当前无 formal peer，需按私聊所属 bot 指定（默认倾向 yushu 也只是 convention_soft）  
4. e2e 测试行 `private:wavememory_e2e` 可忽略或 quarantine  

## 授权句示例

- `private 全部用 yushu formalize`  
- `private 先不动`  
