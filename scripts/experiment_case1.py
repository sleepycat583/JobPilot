"""Week2 Case 1：Python 后端简单 JD 的最小对比实验。

本脚本直接比较单 Prompt baseline 与现有 JD Worker，不经过 Supervisor、Graph、
Chroma 或公司搜索。这样统计的是 JD 解析边界内的调用成本，不能代表完整生产链路成本。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.agents.jd_parser import CONTENT_INSUFFICIENT_CODE, EXTRACTION_UNAVAILABLE_CODE, jd_parser_node
from app.db import Base, build_session_factory, create_sqlalchemy_engine
from app.db.models import ExperimentRun
from app.config import load_settings
from app.providers.chat_model import build_chat_model
from app.repositories.experiment import ExperimentRunRepository
from app.schemas.jd import JDParsed
from app.services.structured_output import StructuredPromptContext, call_with_structured_output


CASE_NAME = "case1_simple_python_backend_jd"
PROMPT_VERSION = "case1-v1"
DEFAULT_DATABASE_PATH = ROOT_DIR / "data" / "experiments.sqlite3"
RUNS_PER_ARCHITECTURE = 3

CASE1_JD = """岗位：Python 后端工程师
我们正在招聘一名 Python 后端工程师，负责面向企业客户的业务平台和数据服务开发。你将参与订单、账户、权限和任务调度等核心模块设计，与产品、前端及测试同学协作，将需求拆分为可维护的 API、异步任务和监控告警方案。团队当前服务运行在 Linux 环境，使用 Python、FastAPI、PostgreSQL、Redis、Docker、Git 和 CI/CD 流程，重视代码评审、单元测试、线上问题复盘及性能优化。

岗位职责：负责 RESTful API 设计与实现，维护 PostgreSQL 数据模型和 SQL 性能，建设 Redis 缓存与消息消费能力；参与 Celery 异步任务、日志监控和故障排查；为关键接口编写单元测试和接口测试，并与前端联调交付稳定版本。要求 3 年及以上 Python 后端开发经验，熟练掌握 Python、FastAPI 或 Django、PostgreSQL、Redis，理解 HTTP、并发、事务、索引和常见缓存策略；熟悉 Linux、Git、Docker 及 CI/CD，能够独立定位线上问题并给出复盘。具备 Kafka、Kubernetes、AWS 或微服务治理经验者优先；有高并发业务、支付或 SaaS 平台经验者加分。本科及以上学历，计算机或相关专业。"""


@dataclass
class CountingChatModel:
    """记录 prompt/response 文本，用于统计实际调用次数和启发式 token 估算。"""

    wrapped: Any
    call_count: int = 0
    token_text: str = ""
    prompts: list[str] = field(default_factory=list)

    def invoke(self, prompt: str) -> Any:
        """转发模型调用并累积本次请求与响应的可见文本。"""

        self.call_count += 1
        self.prompts.append(prompt)
        response = self.wrapped.invoke(prompt)
        content = response if isinstance(response, str) else getattr(response, "content", "")
        self.token_text += prompt + str(content)
        return response

    def bind(self, **kwargs: Any) -> "CountingChatModel":
        """保留计数器，以便结构化输出最后一次低温重试仍计入同一 run。"""

        if hasattr(self.wrapped, "bind"):
            self.wrapped = self.wrapped.bind(**kwargs)
        return self


def _estimate_tokens(text: str) -> int:
    """按非 ASCII 字符 1、ASCII 字符 4 个约 1 token 的公开启发式估算。"""

    non_ascii = sum(ord(char) > 127 for char in text)
    return non_ascii + (len(text) - non_ascii + 3) // 4


def _baseline_prompt(jd_text: str) -> str:
    """生成 baseline 的单 Agent Prompt，显式对齐两组的技能证据要求。"""

    return (
        "You are a single-agent JD parser. Return only JSON matching JDParsed. "
        "Extract the job title, seniority, responsibilities, skills, experience, education, interview focus, ambiguities, and source language. "
        "Return raw JSON only, with no markdown code fences, no ```json wrapper, and no explanatory text before or after the JSON object. "
        "\n"
        "Field constraints:\n"
        "- seniority must be exactly one of: intern, junior, mid, senior, lead, unknown — do not use free-text experience descriptions like '3年及以上' for this field.\n"
        "- For each skill: category must be exactly one of: language, framework, database, cloud, engineering, domain, soft_skill\n"
        "- For each skill: priority must be exactly one of: must, preferred, inferred — use 'must' only for explicit hard requirements; use 'preferred' for 优先 or 加分; use 'inferred' only for clear implications.\n"
        "- For each skill: evidence must be a short quote that can be located verbatim in the original JD text.\n"
        "\n"
        "Do not call tools or use external context.\n"
        f"JD text:\n{jd_text}"
    )


def _unsupported_skill_claim_count(parsed: JDParsed, jd_text: str) -> int:
    """统计技能 evidence 为空或不在原文中的结论数；这是严格子串口径，可能有假阳性。"""

    return sum(not skill.evidence.strip() or skill.evidence not in jd_text for skill in parsed.skills)


def _run_baseline(chat_model: Any, jd_text: str) -> tuple[str, bool, JDParsed | None, list[str], int, int]:
    """运行单 Prompt baseline，返回状态、校验结果、产物、错误码及调用/Token统计。"""

    counted = CountingChatModel(chat_model)
    try:
        result = call_with_structured_output(
            counted,
            JDParsed,
            StructuredPromptContext(full_prompt=_baseline_prompt(jd_text), minimal_input=jd_text),
            "experiment_case1_baseline",
        )
    except Exception as exc:
        return "failed", False, None, [type(exc).__name__], counted.call_count, _estimate_tokens(counted.token_text)
    error_codes = [entry["code"] for entry in result.error_log]
    status = "degraded" if result.degraded or result.value is None else "success"
    return status, result.value is not None, result.value, error_codes, counted.call_count, _estimate_tokens(counted.token_text)


def _run_multi_agent(chat_model: Any, jd_text: str) -> tuple[str, bool, JDParsed | None, list[str], int, int]:
    """调用现有 JD Worker，按 Task 1 错误码把正常、降级和失败运行分开。"""

    counted = CountingChatModel(chat_model)
    try:
        update = jd_parser_node({"user_input": jd_text, "allow_web_search": False}, counted)
    except Exception as exc:
        return "failed", False, None, [type(exc).__name__], counted.call_count, _estimate_tokens(counted.token_text)
    parsed = update.get("jd_parsed")
    error_codes = [entry["code"] for entry in update.get("error_log", []) if isinstance(entry, dict) and "code" in entry]
    is_degraded = bool({EXTRACTION_UNAVAILABLE_CODE, CONTENT_INSUFFICIENT_CODE} & set(error_codes))
    status = "degraded" if is_degraded else "success"
    return status, isinstance(parsed, JDParsed), parsed if isinstance(parsed, JDParsed) else None, error_codes, counted.call_count, _estimate_tokens(counted.token_text)


def run_case1_experiment(
    chat_model: Any,
    *,
    database_path: Path = DEFAULT_DATABASE_PATH,
    model_name: str,
    repeats: int = RUNS_PER_ARCHITECTURE,
    jd_text: str = CASE1_JD,
) -> list[dict[str, object]]:
    """运行 Case 1 的两组对照并写入业务 SQLite，返回本轮运行记录供汇总或测试使用。"""

    database_path.parent.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []
    engine = create_sqlalchemy_engine(f"sqlite:///{database_path.as_posix()}")
    Base.metadata.create_all(engine)
    session_factory = build_session_factory(engine)
    try:
        for architecture, runner in (("baseline", _run_baseline), ("multi_agent", _run_multi_agent)):
            prefix = (
                ExperimentRun.case_name == CASE_NAME,
                ExperimentRun.architecture == architecture,
                ExperimentRun.model_name == model_name,
                ExperimentRun.prompt_version == PROMPT_VERSION,
            )
            with session_factory() as session:
                current_max = session.scalar(select(func.max(ExperimentRun.run_index)).where(*prefix)) or 0
            for offset in range(1, repeats + 1):
                run_index = current_max + offset
                start = time.perf_counter()
                status, schema_valid, parsed, error_codes, llm_calls, estimated_tokens = runner(chat_model, jd_text)
                row: dict[str, object] = {
                    "case_name": CASE_NAME,
                    "architecture": architecture,
                    "run_index": run_index,
                    "model_name": model_name,
                    "prompt_version": PROMPT_VERSION,
                    "status": status,
                    "schema_valid": int(schema_valid),
                    "unsupported_skill_claims": _unsupported_skill_claim_count(parsed, jd_text) if parsed else None,
                    "llm_calls": llm_calls,
                    "estimated_tokens": estimated_tokens,
                    "latency_ms": round((time.perf_counter() - start) * 1000, 2),
                    "error_codes": json.dumps(error_codes, ensure_ascii=False),
                    "output_json": json.dumps(parsed.model_dump(mode="json"), ensure_ascii=False) if parsed else None,
                    "created_at": datetime.now(timezone.utc),
                }
                with session_factory() as session:
                    ExperimentRunRepository(session).create_run(row)
                records.append(row)
    finally:
        engine.dispose()
    return records


def format_summary(records: list[dict[str, object]]) -> str:
    """生成 Case 1 的纯文本汇总；Schema 成功率只计 status=success 的正常运行。"""

    lines = [
        "Case 1: Simple Python backend JD",
        "unsupported_skill_claims: strict evidence substring matching; minor paraphrases can be false positives.",
        "Scope: both branches bypass Supervisor routing; LLM calls/tokens exclude production Supervisor overhead.",
        "architecture | normal schema success | avg unsupported skills | total LLM calls | estimated tokens | degraded runs",
        "-------------|-----------------------|------------------------|-----------------|------------------|---------------",
    ]
    for architecture in ("baseline", "multi_agent"):
        group = [record for record in records if record["architecture"] == architecture]
        total = len(group)
        normal_success = sum(record["status"] == "success" and record["schema_valid"] == 1 for record in group)
        evidence_values = [record["unsupported_skill_claims"] for record in group if record["unsupported_skill_claims"] is not None]
        average_unsupported = "n/a" if not evidence_values else f"{sum(evidence_values) / len(evidence_values):.2f}"
        lines.append(
            f"{architecture:12} | {normal_success}/{total} ({normal_success / total:.0%}) | {average_unsupported:22} | "
            f"{sum(int(record['llm_calls']) for record in group):15} | {sum(int(record['estimated_tokens']) for record in group):16} | "
            f"{sum(record['status'] == 'degraded' for record in group)}"
        )
    degraded = [record for record in records if record["status"] == "degraded"]
    lines.append("degraded run details: none" if not degraded else "degraded run details: " + "; ".join(f"{item['architecture']}#{item['run_index']} {item['error_codes']}" for item in degraded))
    return "\n".join(lines)


def main() -> None:
    """加载现有 Provider 配置，运行三次对照并打印汇总。"""

    parser = argparse.ArgumentParser(description="Run Week2 Case 1 JD parsing comparison")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE_PATH)
    parser.add_argument("--repeats", type=int, default=RUNS_PER_ARCHITECTURE)
    arguments = parser.parse_args()
    if arguments.repeats < 1:
        raise SystemExit("--repeats must be at least 1")
    settings = load_settings()
    records = run_case1_experiment(
        build_chat_model(settings),
        database_path=arguments.database,
        model_name=settings.model_name,
        repeats=arguments.repeats,
    )
    print(format_summary(records))


if __name__ == "__main__":
    main()