"""天窗时段的唯一口径：解析、校验、展示都收在这里。

提交申请、开始作业、销记三处入口共用 evaluate_period 的同一份结论，
列表与详情共用 format_period 的同一份展示，避免同一张天窗在不同环节
被不同规则一会儿放行、一会儿拦下。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

# 允许的时段连接写法：~、～、至、—、–
_SPLIT_PATTERN = re.compile(r"\s*(?:~|～|至|—|–)\s*")
# 日期：2026-9-1 / 2026/09/01 / 20260901
_DATE_PATTERN = re.compile(r"\d{4}[-/.年]?\d{1,2}[-/.月]?\d{1,2}日?")
_TIME_PATTERN = re.compile(r"\d{1,2}:\d{2}(?::\d{2})?")

EARLY_MESSAGE = "实际时段开始时间早于计划时段开始时间，不能开始作业或销记"
END_MISSING_MESSAGE = "实际时段缺少结束时间，无法销记，请补全实际起止时间"


@dataclass(frozen=True)
class Period:
    """解析后的时段；start 为 None 表示完全无法识别。"""

    start: datetime | None = None
    end: datetime | None = None
    # 只填了开始时间、结束时间缺失
    end_missing: bool = False
    # 全天窗口（只给了日期，没有时分）
    all_day: bool = False
    raw: str = ""

    @property
    def present(self) -> bool:
        return self.start is not None


def _parse_dt(text: str) -> datetime | None:
    text = text.strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d",
                "%Y年%m月%d日 %H:%M", "%Y年%m月%d日", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def parse_period(value: Any) -> Period:
    """把「计划时段/实际时段」的各种写法解析成同一结构。

    - 单个日期（如 2026-09-01）按全天窗口处理，不算结束时间缺失；
    - 开始时间完整但缺少结束时间时标记 end_missing，销记环节统一说明原因；
    - 完全无法识别时 start 为 None。
    """
    raw = str(value or "").strip()
    if not raw:
        return Period(raw=raw)

    parts = _SPLIT_PATTERN.split(raw)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        start = _parse_dt(parts[0])
        end = _parse_dt(parts[1])
        # 结束只写了时分（如「04:00」）时，沿用开始日期补齐
        if start is not None and end is None:
            clock = _TIME_PATTERN.fullmatch(parts[1].strip())
            if clock:
                end = _parse_dt(f"{start.strftime('%Y-%m-%d')} {clock.group(0)}")
        return Period(start=start, end=end, raw=raw)

    # 「日期 时间」这类只写到开始时间的写法，结束时间缺失
    whole = _parse_dt(raw)
    if whole is not None:
        has_clock = bool(_TIME_PATTERN.search(raw))
        return Period(start=whole, end_missing=has_clock, all_day=not has_clock, raw=raw)

    # 退化情况：只有一个可解析的日期/时间片段
    match = _DATE_PATTERN.search(raw)
    if match:
        start = _parse_dt(match.group(0))
        if start is not None:
            return Period(start=start, end_missing=bool(_TIME_PATTERN.search(raw)),
                          all_day=not _TIME_PATTERN.search(raw), raw=raw)
    clock = _TIME_PATTERN.search(raw)
    if clock:
        start = _parse_dt(clock.group(0))
        if start is not None:
            return Period(start=start, end_missing=True, raw=raw)
    return Period(raw=raw)


@dataclass(frozen=True)
class PeriodVerdict:
    """时段校验结论：三个入口共用的同一份判断。"""

    allowed: bool
    reason: str = ""


def evaluate_period(
    *,
    planned: Any,
    actual: Any,
    require_actual_end: bool = False,
) -> PeriodVerdict:
    """校验计划时段与实际时段的关系。

    规则只有一份，提交申请、开始作业、销记都走这里：
    - 实际时段开始早于计划时段开始：任何入口都不允许，原因统一说明；
    - require_actual_end（销记）时，实际时段缺结束时间统一说明原因；
    - 计划或实际时段尚未填写、或老数据是全天日期时，不做关系拦截。
    """
    planned_period = parse_period(planned)
    actual_period = parse_period(actual)

    if planned_period.present and actual_period.present:
        if actual_period.start < planned_period.start:
            return PeriodVerdict(False, EARLY_MESSAGE)

    if require_actual_end:
        if not actual_period.present:
            return PeriodVerdict(False, END_MISSING_MESSAGE)
        if actual_period.end_missing:
            return PeriodVerdict(False, END_MISSING_MESSAGE)

    return PeriodVerdict(True)


def _format_dt(value: datetime, date_only: bool) -> str:
    if date_only:
        return value.strftime("%Y-%m-%d")
    return value.strftime("%Y-%m-%d %H:%M")


def format_period(value: Any) -> str:
    """时段的唯一展示口径：列表、详情、动作回显都用这一份。"""
    period = parse_period(value)
    if not period.present:
        return period.raw if period.raw else "—"
    # 只有「只给了日期」的全天窗口才省略时分；显式写到 00:00 的范围仍展示时间
    date_only = period.all_day and period.end is None
    start_text = _format_dt(period.start, date_only)
    if period.end is None:
        return start_text
    return f"{start_text} ~ {_format_dt(period.end, False)}"
