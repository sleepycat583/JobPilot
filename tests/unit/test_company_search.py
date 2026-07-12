"""公司背景搜索 Tool 测试。"""

from __future__ import annotations

from app.tools.company_search import CompanySearchItem, search_company_background


def test_company_search_limits_final_results_to_five() -> None:
    def backend(_: str, limit: int) -> list[CompanySearchItem]:
        assert limit == 5
        return [
            CompanySearchItem(
                title=f"title-{index}",
                url=f"https://example.com/{index}",
                snippet=f"snippet-{index}",
                fetched_at=f"2026-07-12T00:00:0{index}+00:00",
            )
            for index in range(8)
        ]

    result = search_company_background("字节跳动", backend)

    assert result.degraded is False
    assert result.attempts == 1
    assert len(result.items) == 5


def test_company_search_retries_once_and_degrades_without_blocking() -> None:
    attempts = {"count": 0}

    def backend(_: str, __: int) -> list[CompanySearchItem]:
        attempts["count"] += 1
        raise TimeoutError("timeout")

    result = search_company_background("字节跳动", backend)

    assert attempts["count"] == 2
    assert result.degraded is True
    assert result.attempts == 2
    assert result.items == []


def test_company_search_returns_at_most_five_after_retry_success() -> None:
    attempts = {"count": 0}

    def backend(_: str, limit: int) -> list[CompanySearchItem]:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise TimeoutError("timeout")
        assert limit == 5
        return [
            CompanySearchItem(
                title=f"title-{index}",
                url=f"https://example.com/{index}",
                snippet=f"snippet-{index}",
                fetched_at=f"2026-07-12T00:00:0{index}+00:00",
            )
            for index in range(8)
        ]

    result = search_company_background("字节跳动", backend)

    assert attempts["count"] == 2
    assert result.degraded is False
    assert result.attempts == 2
    assert len(result.items) == 5