"""天窗作业业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services.window_period import evaluate_period, format_period
from app.store import store

MODULE = "window"
REQUIRED_FIELDS = ["天窗编号", "作业类型", "作业区段"]
STATUS_ORDER = ["待申请", "已批复", "作业中", "已销记"]
ACTION_RULES = {"提交申请": "已批复", "开始作业": "作业中", "销记天窗": "已销记"}
NEGATIVE_ACTIONS = []
CLOSED_STATUS = STATUS_ORDER[-1]

PERIOD_FIELDS = ["计划时段", "实际时段"]


def serialize_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """列表、详情、导出、动作回显共用的展示口径：时段只按这一份输出。"""
    result = dict(entry)
    for field in PERIOD_FIELDS:
        if field in result:
            result[field] = format_period(result.get(field))
    return result


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
        return [serialize_entry(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return serialize_entry(entry) if entry is not None else None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return serialize_entry(entry), []

    def run_action(
        self,
        entry_id: int,
        action: str,
        values: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"天窗计划 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于天窗作业可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"

        current = str(entry.get("status") or "")
        # 已销记是终态：不能再被改回作业中（其余回退动作同样不放行）。
        if current == CLOSED_STATUS and target != CLOSED_STATUS:
            if target == "作业中":
                return None, "天窗已销记，不能重新开始作业"
            return None, f"天窗已销记，不能回退到「{target}」"

        values = values or {}
        actual_period = values.get("实际时段")
        if actual_period is not None and str(actual_period).strip():
            entry["实际时段"] = actual_period

        # 提交申请、开始作业、销记共用同一份时段结论；
        # 只有销记要求实际时段必须给出结束时间。
        verdict = evaluate_period(
            planned=entry.get("计划时段"),
            actual=entry.get("实际时段"),
            require_actual_end=action == "销记天窗",
        )
        if not verdict.allowed:
            return None, verdict.reason

        entry["status"] = target
        entry["pending"] = target != CLOSED_STATUS
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return serialize_entry(entry), f"天窗计划已{action}"
