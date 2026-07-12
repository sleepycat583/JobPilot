"""公司背景搜索 Tool。

本文件提供 JD 解析 Agent 调用的受限联网搜索能力：
- 只负责执行确定性的公司背景搜索，不参与业务路由或 JD 抽取决策
- 通过依赖注入接收 backend，便于单元测试 mock，避免真实网络请求
- 单次失败后最多重试 1 次；两次都失败时降级为空结果，不中断 JD Agent 主流程
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Sequence


@dataclass(frozen=True)
class CompanySearchItem:
    """单条公司背景搜索结果。"""

    title: str
    url: str
    snippet: str
    fetched_at: str


@dataclass(frozen=True)
class CompanySearchResult:
    """公司背景搜索结果集合。

    参数：
        items: 最终可用的搜索结果，整体最多 5 条。
        degraded: 是否发生了重试耗尽后的降级。
        attempts: 实际尝试次数，取值为 1 或 2。
    """

    items: list[CompanySearchItem]
    degraded: bool
    attempts: int


SearchBackend = Callable[[str, int], Sequence[CompanySearchItem]]
MAX_COMPANY_SEARCH_RESULTS = 5
MAX_COMPANY_SEARCH_ATTEMPTS = 2


def search_company_background(company_name: str, search_backend: SearchBackend) -> CompanySearchResult:
    """搜索公司背景信息。

    参数：
        company_name: 已由上游业务节点确认的公司名。
        search_backend: 真实搜索实现或测试替身，签名为 `(company_name, limit) -> results`。

    返回：
        CompanySearchResult：成功时返回最多 5 条结构化结果；连续失败时返回降级空结果。
    """

    last_error: Exception | None = None

    for attempt in range(1, MAX_COMPANY_SEARCH_ATTEMPTS + 1):
        try:
            raw_items = search_backend(company_name, MAX_COMPANY_SEARCH_RESULTS)
            items = [_normalize_item(item) for item in raw_items][:MAX_COMPANY_SEARCH_RESULTS]
            return CompanySearchResult(items=items, degraded=False, attempts=attempt)
        except Exception as exc:  # noqa: BLE001 - Tool 层统一降级，不向上抛出阻塞异常。
            last_error = exc

    _ = last_error
    return CompanySearchResult(items=[], degraded=True, attempts=MAX_COMPANY_SEARCH_ATTEMPTS)


def _normalize_item(item: CompanySearchItem) -> CompanySearchItem:
    """标准化搜索结果，确保抓取时间存在。"""

    fetched_at = item.fetched_at or datetime.now(timezone.utc).isoformat()
    return CompanySearchItem(
        title=item.title,
        url=item.url,
        snippet=item.snippet,
        fetched_at=fetched_at,
    )