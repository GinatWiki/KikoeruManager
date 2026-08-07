"""通用密码入库服务，供网盘文本自动提取和批量导入复用。"""

import uuid
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from ..models.database import PasswordEntry
from .password_utils import normalize_password_value

AUTO_SOURCE = "auto"
AUTO_DESCRIPTION = "从网盘分享文本自动提取"


def upsert_generic_passwords(
    db: Session,
    passwords: Optional[List[str]] = None,
    *,
    source: str = AUTO_SOURCE,
    description: str = AUTO_DESCRIPTION,
) -> Dict[str, object]:
    """把密码作为通用条目写入密码库，已存在时跳过。"""
    normalized: List[str] = []
    seen: set[str] = set()
    for value in passwords or []:
        password = normalize_password_value(value)
        if password and password not in seen:
            normalized.append(password)
            seen.add(password)

    entries: List[Dict[str, object]] = []
    imported = 0
    skipped = 0
    for password in normalized:
        existing = db.query(PasswordEntry).filter(PasswordEntry.password == password).first()
        if existing:
            skipped += 1
            entries.append({"password": password, "status": "skipped", "reason": "已存在"})
            continue
        db.add(
            PasswordEntry(
                id=str(uuid.uuid4()),
                password=password,
                source=source,
                description=description,
            )
        )
        imported += 1
        entries.append({"password": password, "status": "success"})

    db.commit()
    return {
        "imported": imported,
        "skipped": skipped,
        "passwords": normalized,
        "entries": entries,
    }
