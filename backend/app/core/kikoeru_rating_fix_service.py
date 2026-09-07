"""Kikoeru 数据库评分修复服务（v2.6）

扫描目标（两类，实测真库共 232 个）：
- 0 分作品（rate_average_2dp 为 0/NULL）：重抓修复；
- 满分 5 分作品（rate_average_2dp = 5）：复核校验——评价数过少极易拉出满分偏差，
  必须用日文原版评分校验后才能采信。

抓取结果三类严格区分（网络失败绝不与"确认无评分"混淆）：
- 成功：按规则回填；
- 确认无数据：HTTP 404 / 响应无评分字段（服务端明确响应，网络是通的）
  → 本体无数据时继续试关联版本；全部无数据 → plan='none'，不写库；
- 网络失败：传输层异常（超时/连接失败/非200/JSON解析失败/熔断）
  → 作品级自动重试 2 轮 → 仍失败 plan='error'，**绝不写库**，等手动重试。

重试层次：
1. 请求级：dlsite_service._guarded_get 每次HTTP自动重试3次（2s/4s/8s）+ 一次性客户端兜底 + 熔断器 + 限流抖动；
2. 作品级：DLsiteNetworkError 后自动重试 2 轮（1s/2s 退避）；
3. 手动级：前端「重试失败项」对 error 行精准重新预览并原位替换；apply 自动跳过 error 行；
   评分修复幂等，修好的行下次不再进名单，error/none 行下次重跑自然重试。

回填字段：rate_count / rate_average_2dp / rate_count_detail / rank / review_count / dl_count / price。
写入走 KikoeruDbService 快照写事务；不自动改 is_custom_meta
（评分是 Kikoeru 会随时间刷新的字段，冻结反而阻止后续更新）。

t_work.id → workno：id >= 2000000000000 为 VJ 作品（数值 = id - 偏移）；
补零位数有歧义，优先从 dir 提取 workno（99% 覆盖），提不到按数值生成多候选依次尝试。
"""
import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from .dlsite_service import DLsiteNetworkError, get_dlsite_service
from .kikoeru_db_service import KikoeruDbService
from .rjcode_utils import extract_rjcode

logger = logging.getLogger(__name__)

_VJ_OFFSET = 2000000000000
_RATING_FIELDS = ("rate_count", "rate_average_2dp", "rate_count_detail", "rank", "review_count", "dl_count", "price")


def workno_candidates_from_work_id(work_id: int) -> List[str]:
    """t_work.id 推导 DLsite workno 候选（从 dir 提取不到时的兜底）。"""
    if work_id is None or work_id <= 0:
        return []
    if work_id >= _VJ_OFFSET:
        num = work_id - _VJ_OFFSET
        prefix = "VJ"
    else:
        num = work_id
        prefix = "RJ"
    text = str(num)
    candidates = [f"{prefix}{text.zfill(8)}", f"{prefix}{text.zfill(6)}", f"{prefix}{text}"]
    seen, ordered = set(), []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            ordered.append(c)
    return ordered


def _json_text(value: Any) -> Any:
    """rate_count_detail / rank 在 t_work 里是 TEXT 存 JSON 字符串。"""
    if isinstance(value, str):
        return value
    return json.dumps(value or [], ensure_ascii=False)


class KikoeruRatingFixService:
    """评分修复：扫描 0 分 + 满分作品，预览 + 回填。"""

    def __init__(self, db_service: Optional[KikoeruDbService] = None):
        self._db = db_service or KikoeruDbService()

    # ------------------------------------------------------------ workno 反解
    def _resolve_workno_candidates(self, work_id: int, dir_name: str) -> List[str]:
        """优先 dir 提取（99% 覆盖），兜底 id 推导多候选。"""
        from_dir = extract_rjcode(dir_name)
        if from_dir:
            return [from_dir]
        return workno_candidates_from_work_id(work_id)

    # ------------------------------------------------------------ 目标筛选
    def find_fix_targets(self, ids: Optional[List[Any]] = None,
                         limit: int = 500) -> List[Dict[str, Any]]:
        """查修复目标：0 分（0/NULL）与满分（>=5）作品，附 target_kind。"""
        conn = self._db._connect(readonly=True)
        try:
            if ids:
                marks = ",".join(["?"] * len(ids))
                rows = conn.execute(
                    f'SELECT id, dir, title, rate_count, rate_average_2dp FROM "t_work" '
                    f'WHERE id IN ({marks})',
                    list(ids),
                ).fetchall()
            else:
                rows = conn.execute(
                    'SELECT id, dir, title, rate_count, rate_average_2dp FROM "t_work" '
                    'WHERE rate_average_2dp IS NULL OR rate_average_2dp = 0 OR rate_average_2dp >= 5 '
                    'ORDER BY id LIMIT ?',
                    (max(int(limit or 500), 1),),
                ).fetchall()
            result = []
            for r in rows:
                item = dict(r)
                avg = r["rate_average_2dp"]
                item["target_kind"] = "perfect" if (avg is not None and avg >= 5) else "zero"
                result.append(item)
            return result
        finally:
            conn.close()

    # ------------------------------------------------------------ 抓取（作品级自动重试）
    @staticmethod
    async def _fetch_rating_with_retry(dlsite, workno: str, attempts: int = 3) -> Optional[Dict]:
        """抓单个 workno 的评分；网络异常自动重试（作品级），穷尽后抛 DLsiteNetworkError。

        Returns:
            dict = 拿到评分；None = 服务端确认无数据（404/无评分字段）。
        Raises:
            DLsiteNetworkError: 自动重试穷尽后仍是网络失败。
        """
        last_exc: Optional[Exception] = None
        for attempt in range(max(int(attempts), 1)):
            try:
                return await dlsite.get_product_rating(workno)
            except DLsiteNetworkError as exc:
                last_exc = exc
                if attempt < attempts - 1:
                    await asyncio.sleep(1.0 * (attempt + 1))  # 1s / 2s
        raise DLsiteNetworkError(str(last_exc or "未知网络错误"))

    # ------------------------------------------------------------ 预览
    async def preview_fix(self, ids: Optional[List[Any]] = None,
                          limit: int = 300) -> Dict[str, Any]:
        """逐作品生成修复计划。

        plan 取值：
        - own    本体重抓评分可信（满分已经过原版校验）；
        - linked 本体无评分/满分存疑，套用其他版本（日文原版优先）；
        - none   确认无人评分（网络正常前提下的全版本 0 分/无数据），不写库；
        - error  网络失败（已自动重试穷尽），不写库，等待手动重试。
        """
        targets = self.find_fix_targets(ids=ids, limit=max(int(limit or 300), 1))
        dlsite = get_dlsite_service()
        semaphore = asyncio.Semaphore(5)

        async def plan_one(row: Dict[str, Any]) -> Dict[str, Any]:
            work_id = row["id"]
            dir_name = row["dir"] or ""
            async with semaphore:
                candidates = self._resolve_workno_candidates(work_id, dir_name)
                item = {
                    "id": work_id,
                    "dir": dir_name,
                    "title": row["title"],
                    "target_kind": row["target_kind"],
                    "current_rate_count": row["rate_count"],
                    "current_rate_average_2dp": row["rate_average_2dp"],
                    "plan": "none",
                    "source_rjcode": "",
                    "source_lang": "",
                    "source_work_type": "",
                    "reason": "",
                    "fix": None,
                }

                # ① 本体重抓（网络失败直接 error，不再试关联——网络都不通了）
                own_rating: Optional[Dict[str, Any]] = None
                try:
                    for workno in candidates:
                        own_rating = await self._fetch_rating_with_retry(dlsite, workno)
                        if own_rating is not None:
                            break
                except DLsiteNetworkError as exc:
                    item["plan"] = "error"
                    item["reason"] = f"网络失败（已自动重试）：{exc}"
                    return item

                # ② 本体有有效评分（rate_count > 0；None 或 0 都视为"本体无有效评分"→走③）
                if own_rating and (own_rating.get("rate_count") or 0) > 0:
                    own_avg = float(own_rating.get("rate_average_2dp") or 0)
                    if own_avg >= 5.0:
                        # 满分（含 0 分作品重抓得满分 / 满分复核）→ 原版校验
                        return await self._resolve_perfect_score(dlsite, item, candidates, own_rating)
                    if row["target_kind"] == "perfect":
                        item["plan"] = "own"
                        item["fix"] = own_rating
                        item["reason"] = (
                            f"满分复核：现评分 {own_avg}（{own_rating.get('rate_count')} 评），"
                            "原 5 分为小样本偏差"
                        )
                        return item
                    item["plan"] = "own"
                    item["fix"] = own_rating
                    return item

                # ③ 本体确认无数据（404/无评分字段）→ 关联版本，日文原版优先
                linked_error = ""
                workno = candidates[0] if candidates else ""
                if workno:
                    try:
                        linked = await dlsite.get_linked_works(workno)
                    except Exception as exc:  # noqa: BLE001
                        linked_error = str(exc)
                        linked = {}
                    ordered = self._order_linked_candidates(linked, own_workno=workno)
                    for cand_workno, info in ordered:
                        try:
                            rating = await self._fetch_rating_with_retry(dlsite, cand_workno)
                        except DLsiteNetworkError as exc:
                            item["plan"] = "error"
                            item["reason"] = f"关联版本 {cand_workno} 抓取失败（已自动重试）：{exc}"
                            return item
                        if rating and (rating.get("rate_count") or 0) > 0:
                            item["plan"] = "linked"
                            item["fix"] = rating
                            item["source_rjcode"] = cand_workno
                            item["source_lang"] = getattr(info, "lang", "") or ""
                            item["source_work_type"] = getattr(info, "work_type", "") or ""
                            item["reason"] = "本体无评分数据，套用关联版本评分"
                            return item
                item["reason"] = linked_error or "本体与其他版本均确认无评分数据（网络正常）"
                return item

        items = await asyncio.gather(*(plan_one(row) for row in targets))
        changed_items = [i for i in items if self._fix_differs(i)]
        counts = {
            "total": len(items),
            "fixable": len(changed_items),
            "own": sum(1 for i in items if i["plan"] == "own"),
            "linked": sum(1 for i in items if i["plan"] == "linked"),
            "none": sum(1 for i in items if i["plan"] == "none"),
            "error": sum(1 for i in items if i["plan"] == "error"),
        }
        return {**counts, "items": items}

    @staticmethod
    def _fix_differs(item: Dict[str, Any]) -> bool:
        """回填值与库中当前值是否有差异（满分复核确认有效时 fix==当前值，不写库）。"""
        fix = item.get("fix")
        if not fix:
            return False
        return (
            float(fix.get("rate_average_2dp") or 0) != float(item.get("current_rate_average_2dp") or 0)
            or int(fix.get("rate_count") or 0) != int(item.get("current_rate_count") or 0)
        )

    @staticmethod
    def _order_linked_candidates(linked: Dict[str, Any], own_workno: str) -> List[Any]:
        """关联版本排序：日文原版 → 其他 original → 翻译版；剔除自身与无 workno 项。"""
        entries = [(workno, info) for workno, info in (linked or {}).items()
                   if workno and workno != own_workno]

        def sort_key(entry):
            workno, info = entry
            work_type = str(getattr(info, "work_type", "") or "")
            lang = str(getattr(info, "lang", "") or "").upper()
            if work_type == "original" and lang == "JPN":
                rank = 0          # 日文原版最高优先
            elif work_type == "original":
                rank = 1
            elif lang == "JPN":
                rank = 2
            else:
                rank = 3
            return (rank, workno)

        return sorted(entries, key=sort_key)

    async def _resolve_perfect_score(self, dlsite, item: Dict[str, Any],
                                     candidates: List[str],
                                     own_rating: Dict[str, Any]) -> Dict[str, Any]:
        """满分 5 分校验：抓日文原版评分比对。

        - 原版同为 5 分 → 5 分有效（若与库中现值不同则回填，相同则跳过）；
        - 原版不是 5 分 → 套用原版评分（不信任本地满分）；
        - 原版不可达/无评分 → 保留本体 5 分，标注「未验证」（不写库）。
        """
        own_avg = float(own_rating.get("rate_average_2dp") or 0)
        workno = candidates[0] if candidates else ""
        try:
            linked = await dlsite.get_linked_works(workno) if workno else {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("[KIKOERU-DB] 满分校验关联链获取失败 %s: %s", workno, exc)
            linked = {}
        for cand_workno, info in self._order_linked_candidates(linked, own_workno=workno):
            if str(getattr(info, "work_type", "") or "") != "original":
                continue
            if str(getattr(info, "lang", "") or "").upper() != "JPN":
                continue
            original = await self._fetch_rating_with_retry(dlsite, cand_workno)
            if not original or (original.get("rate_count") or 0) <= 0:
                continue
            original_avg = float(original.get("rate_average_2dp") or 0)
            if original_avg >= 5.0:
                item["plan"] = "own"
                item["fix"] = own_rating
                item["reason"] = f"满分有效：日文原版 {cand_workno} 同为 5 分"
            else:
                item["plan"] = "linked"
                item["fix"] = original
                item["source_rjcode"] = cand_workno
                item["source_lang"] = "JPN"
                item["source_work_type"] = "original"
                item["reason"] = (
                    f"满分 5 存疑（仅 {own_rating.get('rate_count')} 评），"
                    f"套用日文原版评分 {original_avg}"
                )
            return item
        item["plan"] = "own"
        item["fix"] = own_rating
        item["reason"] = "满分 5 未验证（日文原版不可达或无评分）"
        return item

    # ------------------------------------------------------------ 执行
    async def apply_fix(self, ids: Optional[List[Any]] = None,
                        limit: int = 300) -> Dict[str, Any]:
        """按预览计划回填评分字段（单事务 + 写前快照）。

        只写 fix 与当前值有差异的行；error/none/满分确认有效（值相同）行自动跳过。
        """
        preview = await self.preview_fix(ids=ids, limit=limit)
        targets = [i for i in preview["items"] if self._fix_differs(i)]

        if not targets:
            return {"applied": 0, "total": preview["total"],
                    "skipped": preview["none"] + preview["error"],
                    "error": preview["error"], "failed": 0}

        def _do(conn) -> Dict[str, Any]:
            applied = 0
            for item in targets:
                fix = item["fix"]
                conn.execute(
                    'UPDATE "t_work" SET "rate_count" = ?, "rate_average_2dp" = ?, '
                    '"rate_count_detail" = ?, "rank" = ?, "review_count" = ?, '
                    '"dl_count" = ?, "price" = ? WHERE "id" = ?',
                    (
                        fix.get("rate_count") or 0,
                        fix.get("rate_average_2dp") or 0,
                        _json_text(fix.get("rate_count_detail")),
                        _json_text(fix.get("rank")),
                        fix.get("review_count") or 0,
                        fix.get("dl_count") or 0,
                        fix.get("price"),
                        item["id"],
                    ),
                )
                applied += 1
            return {"applied": applied, "total": preview["total"],
                    "skipped": preview["none"] + preview["error"],
                    "error": preview["error"], "failed": 0}

        result = await asyncio.to_thread(self._db._write_with_snapshot, _do)
        logger.info(
            "[KIKOERU-DB] 评分修复完成: applied=%s own=%s linked=%s none=%s error=%s",
            result["applied"], preview["own"], preview["linked"], preview["none"], preview["error"],
        )
        return result


_service: Optional[KikoeruRatingFixService] = None


def get_kikoeru_rating_fix_service() -> KikoeruRatingFixService:
    global _service
    if _service is None:
        _service = KikoeruRatingFixService()
    return _service
