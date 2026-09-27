import asyncio

import pytest

from app.core.config import Settings, qcc_is_enabled, validate_startup_settings
from app.services.employer_due_diligence import (
    EmployerInvestigationRequest,
    InvestigationStatus,
    idempotency_key,
    investigate,
)
from app.services.qcc_client import QccMcpClient, QccProviderError


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def __call__(self, server, tool_name, arguments, headers, timeout):
        self.calls.append((server, tool_name, arguments, headers, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class DiscoverableTransport(FakeTransport):
    def __init__(self, responses):
        super().__init__(responses)
        self.discovery_calls = 0

    async def list_tools(self, server, headers, timeout):
        self.discovery_calls += 1
        return [{"name": "search"}]


def test_qcc_client_retries_transient_errors_and_uses_bearer_header():
    transport = FakeTransport([RuntimeError("network"), {"ok": True}])
    client = QccMcpClient(
        api_key="secret",
        server_urls={"company": "https://agent.qcc.com/mcp/company/stream"},
        allowlist={"company": {"lookup"}},
        transport=transport,
        retry_backoff_seconds=0,
    )

    result = asyncio.run(client.call_tool("company", "lookup", {"name": "Acme"}))

    assert result == {"ok": True}
    assert len(transport.calls) == 2
    assert transport.calls[-1][3]["Authorization"] == "Bearer secret"


def test_qcc_client_normalizes_bearer_prefix_in_configured_key():
    transport = FakeTransport([{"ok": True}])
    client = QccMcpClient(
        api_key="Bearer secret",
        server_urls={"company": "https://agent.qcc.com/mcp/company/stream"},
        allowlist={"company": {"lookup"}},
        transport=transport,
    )

    result = asyncio.run(client.call_tool("company", "lookup", {"name": "Acme"}))

    assert result == {"ok": True}
    assert transport.calls[0][3]["Authorization"] == "Bearer secret"


def test_qcc_client_rejects_tool_outside_allowlist():
    client = QccMcpClient(
        api_key="secret",
        server_urls={"company": "https://agent.qcc.com/mcp/company/stream"},
        allowlist={"company": {"lookup"}},
        transport=FakeTransport([]),
    )

    with pytest.raises(QccProviderError, match="allowlist"):
        asyncio.run(client.call_tool("company", "risk", {}))


def test_qcc_client_exposes_lifecycle_and_caches_tool_discovery():
    transport = DiscoverableTransport([])
    client = QccMcpClient(
        api_key="secret",
        server_urls={"company": "https://agent.qcc.com/mcp/company/stream"},
        allowlist={"company": {"search"}},
        transport=transport,
    )
    asyncio.run(client.connect())
    assert asyncio.run(client.list_tools("company")) == [{"name": "search"}]
    assert asyncio.run(client.list_tools("company")) == [{"name": "search"}]
    asyncio.run(client.close())
    assert transport.discovery_calls == 1


def test_qcc_client_unwraps_streamable_http_sse_json():
    transport = FakeTransport(["event: message\ndata: {\"result\": {\"content\": [{\"type\": \"text\", \"text\": \"{\\\"ok\\\":true}\"}]}}\n\n"])
    client = QccMcpClient(
        api_key="secret",
        server_urls={"company": "https://agent.qcc.com/mcp/company/stream"},
        allowlist={"company": {"lookup"}},
        transport=transport,
    )
    result = asyncio.run(client.call_tool("company", "lookup", {}))
    assert result == {"ok": True}


def test_investigate_returns_awaiting_confirmation_for_multiple_entities():
    transport = FakeTransport([
        {"data": [{"name": "Acme Shanghai", "creditCode": "9131"}, {"name": "Acme Beijing", "creditCode": "9111"}]}
    ])
    client = QccMcpClient(
        api_key="secret",
        server_urls={"company": "https://agent.qcc.com/mcp/company/stream", "risk": "https://agent.qcc.com/mcp/risk/stream"},
        allowlist={"company": {"search", "get_company_registration_info"}, "risk": {"risk"}},
        transport=transport,
    )

    report = asyncio.run(investigate(EmployerInvestigationRequest(company_name="Acme"), client))

    assert report.status == InvestigationStatus.AWAITING_CONFIRMATION
    assert len(report.candidates) == 2
    assert len(transport.calls) == 1
    assert transport.calls[0][2] == {"searchKey": "Acme"}


def test_investigate_does_not_treat_empty_risks_as_no_risk():
    transport = FakeTransport([
        {"data": {"name": "Acme", "creditCode": "9131", "status": "在业"}},
        {"data": []},
    ])
    client = QccMcpClient(
        api_key="secret",
        server_urls={"company": "https://agent.qcc.com/mcp/company/stream", "risk": "https://agent.qcc.com/mcp/risk/stream"},
        allowlist={"company": {"search"}, "risk": {"risk"}},
        transport=transport,
    )

    report = asyncio.run(investigate(EmployerInvestigationRequest(unified_social_credit_code="9131"), client))

    assert report.status == InvestigationStatus.COMPLETED
    assert report.risk_summary is None
    assert report.risk_data_available is False
    assert transport.calls[0][2] == {"searchKey": "9131"}
    assert transport.calls[1][2] == {"searchKey": "9131"}


def test_qcc_enabled_requires_key_and_official_urls():
    settings = Settings(
        _env_file=None,
        qcc_enabled=True,
        qcc_api_key="",
        qcc_company_mcp_url="https://agent.qcc.com/mcp/company/stream",
        qcc_risk_mcp_url="https://evil.example/mcp/risk",
    )
    with pytest.raises(RuntimeError, match="QCC_API_KEY"):
        validate_startup_settings(settings)


def test_qcc_disabled_does_not_require_credentials():
    validate_startup_settings(Settings(_env_file=None, qcc_enabled=False))


def test_qcc_compatibility_flag_controls_feature_state():
    assert qcc_is_enabled(Settings(_env_file=None, qcc_due_diligence_enabled=False, qcc_enabled=True)) is True
    assert qcc_is_enabled(Settings(_env_file=None, qcc_due_diligence_enabled=True, qcc_enabled=False)) is False


def test_idempotency_key_is_stable_and_changes_with_subject():
    first = idempotency_key(EmployerInvestigationRequest(unified_social_credit_code="9131"))
    assert first == idempotency_key(EmployerInvestigationRequest(unified_social_credit_code="9131"))
    assert first != idempotency_key(EmployerInvestigationRequest(unified_social_credit_code="9111"))


def test_qcc_enabled_rejects_non_official_url_even_with_key():
    settings = Settings(
        _env_file=None,
        qcc_enabled=True,
        qcc_api_key="secret",
        qcc_company_mcp_url="https://evil.example/company",
        qcc_risk_mcp_url="https://agent.qcc.com/mcp/risk/stream",
    )
    with pytest.raises(RuntimeError, match="QCC_COMPANY_MCP_URL"):
        validate_startup_settings(settings)


def test_investigate_distinguishes_quota_exceeded():
    transport = FakeTransport([QccProviderError("quota", kind="quota_exceeded")])
    client = QccMcpClient(
        api_key="secret",
        server_urls={"company": "https://agent.qcc.com/mcp/company/stream", "risk": "https://agent.qcc.com/mcp/risk/stream"},
        allowlist={"company": {"search"}, "risk": {"risk"}},
        transport=transport,
    )
    report = asyncio.run(investigate(EmployerInvestigationRequest(company_name="Acme"), client))
    assert report.status == InvestigationStatus.QUOTA_EXCEEDED


def test_investigate_without_confirmation_always_pauses_single_candidate():
    transport = FakeTransport([{"data": [{"name": "Acme", "creditCode": "9131"}]}])
    client = QccMcpClient(
        api_key="secret",
        server_urls={"company": "https://agent.qcc.com/mcp/company/stream"},
        allowlist={"company": {"search"}},
        transport=transport,
    )
    report = asyncio.run(investigate(EmployerInvestigationRequest(company_name="Acme"), client))
    assert report.status == InvestigationStatus.AWAITING_CONFIRMATION
