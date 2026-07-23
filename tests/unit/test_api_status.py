"""API 线程终态状态映射回归测试。"""

from __future__ import annotations

from app.api import _terminal_error_code


def test_terminal_error_node_is_not_reported_as_completed() -> None:
    """error_node 携带未处理异常时，API 必须把终态视为失败。"""

    state = {
        "current_node": "error_node",
        "error_log": [{"code": "UNHANDLED_NODE_EXCEPTION", "message": "Connection error."}],
    }

    assert _terminal_error_code(state) == "UNHANDLED_NODE_EXCEPTION"


def test_non_terminal_error_log_does_not_force_failure() -> None:
    """可恢复节点错误尚未进入 error_node 时，不能提前中止任务。"""

    state = {
        "current_node": "jd_parser",
        "error_log": [{"code": "JD_EXTRACTION_UNAVAILABLE"}],
    }

    assert _terminal_error_code(state) is None