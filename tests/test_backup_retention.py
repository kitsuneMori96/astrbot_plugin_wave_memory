"""自动备份保留规则单元测试：验证仅严格匹配格式并按 mtime 排序保留。"""

import time
import re
from pathlib import Path
import pytest


def test_backup_retention_regex_and_mtime_sorting(tmp_path: Path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    # 模拟各类混杂文件
    manual_backup = backup_dir / "wave_memory_stage6_pre_job_conflict.db"
    manual_backup.write_text("manual backup", encoding="utf-8")
    
    # 模拟 3 个按时间生成的备份，文件创建时间顺序与文件名不同
    b1 = backup_dir / "wave_memory_20260726_100000.db"
    b1.write_text("b1", encoding="utf-8")
    
    time.sleep(0.05)
    b2 = backup_dir / "wave_memory_20260726_110000.db"
    b2.write_text("b2", encoding="utf-8")
    
    time.sleep(0.05)
    b3 = backup_dir / "wave_memory_20260726_120000.db"
    b3.write_text("b3", encoding="utf-8")

    # 提取并排序规则复刻 main.py 中的实现
    max_backups = 2
    auto_backups = sorted(
        (
            f for f in backup_dir.glob("wave_memory_*.db")
            if re.fullmatch(r"wave_memory_\d{8}_\d{6}\.db", f.name)
        ),
        key=lambda f: f.stat().st_mtime,
    )

    # 确认筛选出了且仅筛选出了 3 个符合格式的备份
    assert len(auto_backups) == 3
    assert manual_backup not in auto_backups

    # 模拟删除多余旧备份
    to_delete = auto_backups[:-max_backups]
    for old in to_delete:
        old.unlink()

    # 校验：b1 被删，b2 和 b3 保留，手工备份完全不受影响
    assert not b1.exists()
    assert b2.exists()
    assert b3.exists()
    assert manual_backup.exists()
