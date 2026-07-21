# 运行时端到端只读冒烟（WebUI / person / config）

日期：2026-07-21  
protected 禁 destructive/fanout 仍 blocked。

## 配置

- `Cross_Group_Settings.cross_group_enabled` = **True**

## WebUI 记忆列表（模拟 group-open）

| 过滤 | 主群 398291136 条数 |
|---|---:|
| group-open（现） | **44074** |
| strict qq:session | **0** |
| strict 羽书:session | 37235 |

## person_search

- 过滤改为 group-open + 同 bot 或空 bot + session 软匹配  
- 样例 QQ `617716259` 本群可计 **4162** 条发言  
- 容器已加载新 `person_search.py` 并重启 Fully init

## deep_search / FTS

- 容器代码 group-open  
- 主群「是谁」open 有命中 / qq:session strict 为 0  

## HNSW

- 新分片 g009/g010 各约 421MB  

## 未做

destructive 清理、fanout promote、批量改 session 编码  
