"""天窗作业业务规则：状态流转、字段校验与筛选口径都收在这里。

时段校验只有一份：提交申请、开始作业、销记天窗走同一个 `check_periods`，
计划时段与实际时段的关系、结束时间缺失的说明都由它给出，三个入口结论一致。
列表与详情都经 `serialize_entry` 输出，时段写法自然也是同一份。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, NamedTuple

from app.store import store

MODULE = "window"
REQUIRED_FIELDS = ["天窗编号", "作业类型", "作业区段"]
STATUS_ORDER = ["待申请", "已批复", "作业中", "已销记"]
ACTION_RULES = {"提交申请": "已批复", "开始作业": "作业中", "销记天窗": "已销记"}
NEGATIVE_ACTIONS = []

PERIOD_FIELDS = ("计划时段", "实际时段")
# 依次匹配「日期+时间」「仅日期」「仅时间」，按出现顺序拼出时段的起点与终点。
_TOKEN_RE = re.compile(
    r"(?P<dt>\d{4}-\d{1,2}-\d{1,2}[ T]\d{1,2}:\d{2})"
    r"|(?P<d>\d{4}-\d{1,2}-\d{1,2})"
    r"|(?P<t>\d{1,2}:\d{2})"
)
_DATETIME_FMT = "%Y-%m-%d %H:%M"


class Period(NamedTuple):
    """一段解析后的时段；end 为 None 表示只写到开始、结束时间缺失。"""

    start: datetime
    end: datetime | None


def _parse_token(match: re.Match[str], base_date: datetime | None) -> datetime | None:
    text = match.group(0)
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d", "%H:%M"):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if fmt == "%H:%M":
            if base_date is None:
                return None
            return parsed.replace(year=base_date.year, month=base_date.month, day=base_date.day)
        return parsed
    return None


def parse_period(raw: Any) -> Period | None:
    """把时段文本解析成起止时间；空文本或无法识别时返回 None。

    支持「2026-09-25 08:00~2026-09-25 12:00」「2026-09-25 08:00~12:00」等写法；
    终点只写时钟时间时沿用起点日期，不晚于起点则按跨天顺延一日。
    """
    text = str(raw or "").strip()
    if not text:
        return None
    points: list[datetime] = []
    clock_only_end = False
    for match in _TOKEN_RE.finditer(text):
        point = _parse_token(match, points[0] if points else None)
        if point is None:
            return None
        points.append(point)
        clock_only_end = match.lastgroup == "t"
    if not points:
        return None
    start = points[0]
    if len(points) < 2:
        return Period(start=start, end=None)
    end = points[-1]
    if clock_only_end and end <= start:
        end += timedelta(days=1)
    return Period(start=start, end=end)


def check_periods(entry: dict[str, Any]) -> str | None:
    """天窗时段的唯一校验口径：三个动作入口共用，通过时返回 None。"""
    plan = parse_period(entry.get("计划时段"))
    if plan is None:
        return "计划时段未填写或无法识别"
    if plan.end is None:
        return "计划时段缺少结束时间"
    actual = parse_period(entry.get("实际时段"))
    if actual is None:
        return None
    if actual.end is None:
        return "实际时段缺少结束时间"
    if actual.start < plan.start:
        return "实际时段早于计划时段"
    return None


def format_period(raw: Any) -> str:
    """把时段文本写成统一样式；列表与详情都走这里，保证是同一份。"""
    period = parse_period(raw)
    if period is None:
        return str(raw or "").strip()
    start = period.start.strftime(_DATETIME_FMT)
    if period.end is None:
        return f"{start} 至 —"
    return f"{start} 至 {period.end.strftime(_DATETIME_FMT)}"


class WindowService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("天窗编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self.serialize_entry(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        return self.serialize_entry(entry)

    def serialize_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        """输出前统一整理：时段字段写成同一份文本，不改动仓库里的原始数据。"""
        item = dict(entry)
        for field in PERIOD_FIELDS:
            item[field] = format_period(item.get(field))
        return item

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for field in PERIOD_FIELDS:
            entry[field] = str(values.get(field) or "").strip()
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return self.serialize_entry(entry), []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"天窗计划 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于天窗作业可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        if entry.get("status") == "已销记" and target == "作业中":
            return None, "已销记的天窗不能改回作业中"
        reason = check_periods(entry)
        if reason is not None:
            return None, f"时段校验未通过：{reason}，请核对后再{action}"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return self.serialize_entry(entry), f"天窗计划已{action}"
