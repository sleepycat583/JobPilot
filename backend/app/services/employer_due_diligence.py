"""Domain service for pre-application employer investigations."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.services.qcc_client import QccMcpClient, QccProviderError, extract_request_id, unwrap_mcp_result


class InvestigationStatus(StrEnum):
    COMPLETED = "completed"
    ENTITY_NOT_FOUND = "entity_not_found"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    PROVIDER_ERROR = "provider_error"
    QUOTA_EXCEEDED = "quota_exceeded"


class EmployerCandidate(BaseModel):
    name: str
    unified_social_credit_code: str | None = None
    status: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class EmployerInvestigationRequest(BaseModel):
    company_name: str | None = None
    unified_social_credit_code: str | None = None
    jd_id: str | None = None
    user_confirmation_required: bool = True


class RiskItem(BaseModel):
    category: str
    title: str
    detail: str | None = None
    severity: str | None = None
    source_ref: str | None = None


class EmployerInvestigationReport(BaseModel):
    subject_name: str | None = None
    unified_social_credit_code: str | None = None
    registration_status: str | None = None
    operation_status: str | None = None
    risk_items: list[RiskItem] = Field(default_factory=list)
    risk_summary: str | None = None
    risk_data_available: bool = False
    candidates: list[EmployerCandidate] = Field(default_factory=list)
    queried_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source_refs: list[str] = Field(default_factory=list)
    provider_request_id: str | None = None
    status: InvestigationStatus = InvestigationStatus.PROVIDER_ERROR
    disclaimer: str = "企查查数据仅供信息核验参考，不构成法律或投资结论。"


def _payload_data(payload: Any) -> Any:
    payload = unwrap_mcp_result(payload)
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def _candidate_list(payload: Any) -> list[EmployerCandidate]:
    data = _payload_data(payload)
    if isinstance(data, dict):
        data = data.get("list") or data.get("items") or data.get("企业信息") or [data]
    if not isinstance(data, list):
        return []
    candidates: list[EmployerCandidate] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("companyName") or item.get("entName") or item.get("企业名称")
        if name:
            code = item.get("creditCode") or item.get("unifiedSocialCreditCode") or item.get("credit_code") or item.get("统一社会信用代码")
            candidates.append(EmployerCandidate(name=str(name), unified_social_credit_code=code, status=item.get("status"), raw=dict(item)))
    return candidates


def _normalize_company(payload: Any) -> tuple[str | None, str | None, str | None, str | None]:
    data = _payload_data(payload)
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        return None, None, None, None
    return (
        data.get("name") or data.get("companyName") or data.get("entName") or data.get("企业名称"),
        data.get("creditCode") or data.get("unifiedSocialCreditCode") or data.get("credit_code") or data.get("统一社会信用代码"),
        data.get("status") or data.get("registrationStatus") or data.get("登记状态") or data.get("状态"),
        data.get("operationStatus") or data.get("businessStatus") or data.get("经营状态"),
    )


def _normalize_risks(payload: Any) -> tuple[list[RiskItem], bool, list[str]]:
    data = _payload_data(payload)
    if isinstance(data, dict):
        items = data.get("list") or data.get("items") or data.get("risks")
        available = data.get("available", items is not None)
    else:
        items = data
        available = bool(items) if isinstance(items, list) else False
    refs: list[str] = []
    risks: list[RiskItem] = []
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            title = item.get("title") or item.get("name") or item.get("riskType")
            if not title:
                continue
            ref = item.get("source") or item.get("url") or item.get("id")
            if ref is not None:
                refs.append(str(ref))
            risks.append(RiskItem(category=str(item.get("category") or item.get("type") or "risk"), title=str(title), detail=item.get("detail") or item.get("description"), severity=item.get("severity"), source_ref=str(ref) if ref is not None else None))
    if not risks and isinstance(data, dict) and isinstance(data.get("搜索结果"), str):
        return [], True, refs
    return risks, bool(available), refs


async def investigate(
    request: EmployerInvestigationRequest,
    client: QccMcpClient,
    *,
    company_tool_name: str = "search",
    company_registration_tool_name: str = "get_company_registration_info",
    risk_tool_name: str = "risk",
) -> EmployerInvestigationReport:
    try:
        if request.unified_social_credit_code:
            # Keep compatibility with providers/tests that expose only the
            # company search tool; production QCC uses the registration tool.
            available_company_tools = getattr(client, "allowlist", {}).get("company", set())
            registration_tool = company_registration_tool_name if company_registration_tool_name in available_company_tools else company_tool_name
            company_payload = await client.call_tool(
                "company",
                registration_tool,
                {"searchKey": request.unified_social_credit_code},
            )
            name, code, reg_status, op_status = _normalize_company(company_payload)
            if not name:
                return EmployerInvestigationReport(unified_social_credit_code=request.unified_social_credit_code, status=InvestigationStatus.ENTITY_NOT_FOUND)
        else:
            if not request.company_name:
                return EmployerInvestigationReport(status=InvestigationStatus.ENTITY_NOT_FOUND)
            company_payload = await client.call_tool("company", company_tool_name, {"searchKey": request.company_name})
            candidates = _candidate_list(company_payload)
            if not candidates:
                return EmployerInvestigationReport(status=InvestigationStatus.ENTITY_NOT_FOUND)
            if len(candidates) != 1 or request.user_confirmation_required:
                return EmployerInvestigationReport(subject_name=request.company_name, candidates=candidates, status=InvestigationStatus.AWAITING_CONFIRMATION)
            chosen = candidates[0]
            name, code, reg_status, op_status = chosen.name, chosen.unified_social_credit_code, chosen.status, None
        if not code:
            code = request.unified_social_credit_code
        if not code:
            return EmployerInvestigationReport(subject_name=name, registration_status=reg_status, operation_status=op_status, status=InvestigationStatus.ENTITY_NOT_FOUND)
        # QCC risk servers use ``searchKey`` for either a credit code or name.
        risk_payload = await client.call_tool("risk", risk_tool_name, {"searchKey": code})
        risk_items, available, refs = _normalize_risks(risk_payload)
        summary = (f"发现 {len(risk_items)} 项风险" if risk_items else None) if available else None
        return EmployerInvestigationReport(subject_name=name, unified_social_credit_code=code, registration_status=reg_status, operation_status=op_status, risk_items=risk_items, risk_summary=summary, risk_data_available=available, source_refs=refs, provider_request_id=extract_request_id(risk_payload), status=InvestigationStatus.COMPLETED)
    except QccProviderError as exc:
        status = InvestigationStatus.QUOTA_EXCEEDED if exc.kind == "quota_exceeded" else InvestigationStatus.PROVIDER_ERROR
        return EmployerInvestigationReport(status=status, provider_request_id=exc.request_id)


def idempotency_key(request: EmployerInvestigationRequest) -> str:
    raw = json.dumps({"company_name": request.company_name, "credit_code": request.unified_social_credit_code, "jd_id": request.jd_id}, ensure_ascii=True, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# Public aliases used by adapters and tests that need normalization without a provider call.
parse_entity_candidates = _candidate_list
normalize_company_result = _normalize_company
normalize_risk_result = _normalize_risks
