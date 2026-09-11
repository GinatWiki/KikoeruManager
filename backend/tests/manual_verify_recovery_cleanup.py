"""恢复区临时目录优先 + 智能清理 + 存量迁移的行为验证（monkeypatch config 路径，不依赖 DB）。"""
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

tmp = Path(tempfile.mkdtemp(prefix="recovery-test-"))
fake_config = tmp / "config" / "config.yaml"
fake_config.parent.mkdir(parents=True, exist_ok=True)
fake_config.write_text("{}", encoding="utf-8")

import app.core.filter_recovery_service as frs

# monkeypatch config 路径：_legacy_recovery_root() 将解析为 tmp/data/filter-recovery
frs.get_config_file_path = lambda: str(fake_config)

new_root = tmp / "temp" / "filter-recovery"
legacy = tmp / "data" / "filter-recovery"

service = frs.FilterRecoveryService(recovery_root=str(new_root))

# 1) recovery_root 优先临时目录
assert service.recovery_root() == new_root.resolve(), service.recovery_root()
print("临时目录优先 OK:", service.recovery_root())

# 2) 构造旧位置数据：3 个任务目录（一个很旧、一个中等、一个最近）
now = time.time()
for name, age_days in (("task-old", 30), ("task-mid", 3), ("task-new", 0)):
    d = legacy / name
    (d / "payload" / "x").mkdir(parents=True)
    (d / "manifest.json").write_text("{}", encoding="utf-8")
    old = now - age_days * 86400
    os.utime(d, (old, old))

# 新位置已有数据（应保留）
(new_root / "task-existing" / "payload").mkdir(parents=True)

# 3) 迁移：legacy → new_root
result = service.migrate_legacy_recovery_root()
assert result["migrated"] == 3, result
assert not any(legacy.iterdir()), "旧目录应搬空"
assert (new_root / "task-old").exists(), "task-old 应出现在新位置"
print("迁移 OK:", result)

# 4) 清理：保留 7 天、上限 20GB、最少保留 3 个、活跃保护 24h
# task-old（30 天）按期删除；task-mid（3 天）保留；task-new/task-existing 受活跃保护
res = service.cleanup_expired(preserve_days=7, max_size_gb=20, min_keep_count=3)
print("清理结果:", res)
assert "task-old" in res["deleted_tasks"], res
assert "task-mid" not in res["deleted_tasks"]
assert res["kept_count"] == 3
assert (new_root / "task-mid").exists()
assert not (new_root / "task-old").exists()
print("清理 OK")

# 5) 最少保留验证：min_keep_count=10、preserve_days=365 → 全保留
res2 = service.cleanup_expired(preserve_days=365, max_size_gb=20, min_keep_count=10)
assert res2["deleted_tasks"] == [], res2
print("最少保留 OK")

# 6) 还原路径兼容：manifest 不含绝对路径，_payload_path 按新 root 动态解析
payload = service._payload_path("task-mid", {"recovery_id": "abc", "name": "x.mp3"})
assert str(payload).startswith(str(new_root)), payload
print("还原路径动态解析 OK:", payload)

shutil.rmtree(tmp, ignore_errors=True)
print("ALL PASS")
