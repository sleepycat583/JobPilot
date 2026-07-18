# Week2 收尾验收报告

## 测试执行结果

| 测试命令 | 完整结果 | 结论 |
| --- | --- | --- |
| `.venv\Scripts\python.exe -m pytest -q` | `244 passed in 39.71s` | 通过 |
| `.venv\Scripts\python.exe -m pytest -m core_agent_tests -q` | `171 passed, 73 deselected in 34.45s` | 通过 |

## §15 MVP 必须验收核对

| 验收项 | 对应测试文件::用例名 | 状态（通过/未覆盖/部分覆盖） | 备注 |
| --- | --- | --- | --- |
| Model Provider | `tests/unit/test_config.py::test_load_settings_fails_when_required_field_is_missing`；`tests/unit/test_chat_model.py::test_build_chat_model_rejects_invalid_provider`；`tests/unit/test_chat_model.py::test_build_chat_model_passes_expected_arguments` | 通过 | 覆盖必填配置、非法 Provider 启动失败及 OpenAI Compatible 模型构造参数。 |
| 统一 Schema | `tests/unit/test_router_schema.py::test_router_schema_rejects_invalid_route`；`tests/unit/test_jd_schema.py::test_skill_requirement_rejects_invalid_priority`；`tests/unit/test_review_schema.py::test_hitl_command_union_rejects_cross_gate_fields`；`tests/unit/test_state_schema.py::test_state_can_hold_independent_business_fields` | 通过 | 覆盖 Router、JD、HITL、State 的 Pydantic/TypedDict 边界；非法枚举在进入下游前被拒绝。 |
| 固定 Embedding | `tests/unit/test_embedding.py::test_build_embedding_model_uses_fixed_bge_m3`；`tests/unit/test_embedding.py::test_build_embedding_model_does_not_expose_model_name_parameter`；`tests/unit/test_chroma_store.py::test_chroma_store_rejects_mismatched_embedding_metadata` | 通过 | 覆盖固定 `BAAI/bge-m3`、不暴露模型名参数、collection metadata 不一致时报错。 |
| RAG 边界 | `tests/unit/test_chroma_store.py::test_chroma_store_query_applies_top_k_and_relevance_threshold`；`tests/unit/test_resume_matcher.py::test_resume_matcher_binds_java_and_spring_boot_to_real_chunk_quote`；`tests/unit/test_resume_matcher.py::test_resume_matcher_caps_empty_rag_result_and_logs_code` | 通过 | 覆盖 top_k=5、阈值 0.35、chunk 引用、空召回 `RAG_EMPTY_RESULT` 与不虚构证据。 |
| Supervisor | `tests/unit/test_supervisor.py::test_supervisor_routes_jd_request_correctly`；`tests/unit/test_supervisor.py::test_supervisor_empty_input_does_not_call_llm`；`tests/unit/test_graph_routing.py::test_unknown_route_falls_back_to_error_node` | 通过 | 覆盖路由、空输入 `INPUT_EMPTY`、非法 route 不进入 Worker。 |
| JD 解析 Agent | `tests/unit/test_jd_parser.py::test_jd_parser_extracts_skills_with_original_evidence`；`tests/unit/test_jd_parser.py::test_jd_parser_without_company_name_never_calls_search`；`tests/unit/test_jd_parser.py::test_jd_parser_does_not_fabricate_skills_for_content_insufficient_jd` | 通过 | 覆盖 priority/evidence、无公司名不搜索、信息不足写 ambiguities 且不编造技能。 |
| 联网搜索 Tool | `tests/unit/test_company_search.py::test_company_search_limits_final_results_to_five`；`tests/unit/test_company_search.py::test_company_search_retries_once_and_degrades_without_blocking`；`tests/unit/test_jd_parser.py::test_jd_parser_authorized_company_search_uses_at_most_five_results` | 通过 | 覆盖授权边界、最多 5 条及失败后重试一次并降级。 |
| 简历 RAG | `tests/unit/test_resume_matcher.py::test_resume_matcher_binds_java_and_spring_boot_to_real_chunk_quote`；`tests/unit/test_resume_matcher.py::test_resume_matcher_caps_empty_rag_result_and_logs_code`；`tests/unit/test_chroma_store.py::test_chroma_store_missing_resume_version_fails_without_embedding_or_query` | 通过 | 覆盖检索证据、空召回、缺失简历版本。 |
| 匹配评分 | `tests/unit/test_match_scoring.py::test_match_scoring_triggers_low_score_review_for_59_9`；`tests/unit/test_match_scoring.py::test_match_scoring_does_not_trigger_low_score_review_for_60_0`；`tests/unit/test_match_scoring.py::test_match_scoring_caps_empty_rag_result_to_ten_points` | 通过 | 覆盖确定性计分、59.9/60.0 Gate 边界和空 RAG 最高 10 分。 |
| 模拟面试 | `tests/unit/test_interview_simulator.py::test_plan_uses_jd_and_match_inputs_in_prompt`；`tests/unit/test_interview_simulator.py::test_evaluate_answer_node_marks_only_current_record_unavailable_after_three_failures`；`tests/unit/test_interview_simulator.py::test_generate_review_report_node_keeps_degraded_report_on_final_review_path` | 通过 | 覆盖计划、问答评价、单题降级不污染历史记录、复盘和最终审核路径。 |
| 面试中途 HITL | `tests/unit/test_graph_builder.py::test_interview_context_update_reinterrupts_same_question`；`tests/integration/test_checkpoint_restart.py::test_interview_recovers_across_three_processes_without_rerunning_supervisor`；`tests/integration/test_api.py::test_resume_rejects_unknown_thread` | 通过 | 覆盖同 thread 的 context_update 恢复、不重跑 Supervisor、错误 thread 返回 checkpoint 未找到。 |
| 低分 HITL | `tests/unit/test_graph_builder.py::test_low_score_gate_sets_review_status_and_stops_before_finalize`；`tests/integration/test_checkpoint_restart.py::test_low_score_revise_inputs_recovers_in_fresh_process_with_second_attempt`；`tests/integration/test_api.py::test_resume_low_score_review_cancel_ends_without_finalization` | 通过 | 覆盖低分中断、revise_inputs 跨进程恢复、cancel 不生成最终结论。 |
| 最终核可 HITL | `tests/unit/test_graph_builder.py::test_high_score_match_requires_final_approval_before_output`；`tests/unit/test_graph_builder.py::test_interview_report_reject_regenerates_only_report_and_preserves_question_records`；`tests/integration/test_checkpoint_restart.py::test_interview_report_final_review_recovers_and_reject_only_rebuilds_report_in_fresh_process` | 通过 | 覆盖 approve 前无 final_output、reject 仅重做目标产物、重启后恢复审核。 |
| State 隔离 | `tests/unit/test_state_schema.py::test_state_can_hold_independent_business_fields`；`tests/unit/test_jd_parser.py::test_jd_parser_writes_only_jd_business_field`；`tests/unit/test_resume_matcher.py::test_resume_matcher_writes_only_match_result_business_field` | 通过 | 覆盖独立业务字段共存及 Worker 不越权覆盖。 |
| Rolling summary | `tests/unit/test_rolling_summary.py::test_message_threshold_is_inclusive_and_retains_six_recent_messages`；`tests/unit/test_rolling_summary.py::test_three_invalid_json_responses_preserve_all_conversation_state`；`tests/integration/test_checkpoint_restart.py::test_rolling_summary_failed_checkpoint_recovery_preserves_all_messages` | 通过 | 覆盖 12 条/8k token 触发、最近 6 条保留、摘要失败和重启恢复不丢原消息。 |
| JSON 恢复（含节点级降级） | `tests/unit/test_structured_output.py::test_structured_output_succeeds_after_two_retries`；`tests/unit/test_structured_output.py::test_structured_output_degrades_after_three_failures`；`tests/unit/test_interview_simulator.py::test_evaluate_answer_node_marks_only_current_record_unavailable_after_three_failures`；`tests/unit/test_interview_simulator.py::test_generate_review_report_degrades_to_deterministic_scores_and_unavailable_text`；`tests/unit/test_graph_builder.py::test_match_unavailable_routes_to_final_review_and_finalizes_serializable_draft` | 通过 | 覆盖第三次成功、三次耗尽、Task 1 的 JD/匹配降级路径，以及 Task 2 的单题评价和面试复盘降级路径。 |
| 日志与可观测性 | `tests/unit/test_observability.py::test_logger_writes_parseable_jsonl_with_stable_fields`；`tests/unit/test_observability.py::test_observer_double_writes_same_returned_error_entry_to_jsonl`；`tests/unit/test_observability.py::test_observer_normalizes_sensitive_error_entry_before_both_writes` | 通过 | 覆盖 JSONL、session/thread 关联字段、ErrorEntry 双写与敏感信息脱敏。 |
| SQLite 职责隔离 | `tests/integration/test_checkpoint_restart.py::test_sqlite_checkpoint_recovers_complete_state_in_fresh_process` | 部分覆盖 | 已覆盖 SqliteSaver 的跨进程恢复；当前仍处 Week2 禁止接入 SQLAlchemy 的阶段，未实现/验证 SQLAlchemy 业务库与 Checkpoint 文件或访问 API 的最终隔离。 |
| Checkpoint | `tests/integration/test_checkpoint_restart.py::test_sqlite_checkpoint_recovers_complete_state_in_fresh_process`；`tests/integration/test_checkpoint_restart.py::test_checkpoint_metadata_preserves_session_id_across_fresh_process`；`tests/integration/test_checkpoint_restart.py::test_interview_final_review_restart_preserves_remaining_task_queue` | 部分覆盖 | 已覆盖状态、session metadata、队列和 HITL 跨进程恢复；未发现 `graph_version` 不兼容时拒绝恢复的自动化用例。 |
| SQLAlchemy | 无 | 未覆盖 | §13/§14 将 SQLAlchemy 定义为 Week2 核心门禁后才可接入的 Week3 工作；当前仓库未接入 SQLAlchemy、无业务数据库 Schema、repository 或对应测试。 |
| React + 基础 SSE | 无 | 未覆盖 | 按 §13 的技术接入顺序属于核心 Agent 门禁通过后才进行的 Week3 工作；当前无 React 项目、SSE 前端订阅和 event_id 对照测试。 |
| interrupt UI | 无 | 未覆盖 | 当前仅有 FastAPI/Graph 层的 interrupt 与 resume 测试；无浏览器表单/弹窗、刷新恢复和前端幂等提交测试。 |
| Git/GitHub | `pyproject.toml::[tool.pytest.ini_options].markers`；本报告中的两条 pytest 执行记录 | 部分覆盖 | 已有 `core_agent_tests` 标记组且两条验收命令通过；当前仓库无 PR 模板、分支保护或自动化校验，无法以测试证明 GitHub 合并规则。 |
| 对比实验 | `tests/unit/test_experiment_case1.py::test_case1_experiment_smoke_writes_baseline_and_multi_agent_runs` | 部分覆盖 | Task 3 仅完成 Case 1：脚本以 baseline 与 JD Worker 各 3 次运行写入 SQLite，保存 Schema、无证据结论、调用数、估算 token、延迟、错误码和原始输出。Case 2/3、3 Case x 2 架构 x 3 次的 18 条记录、人工 rubric、双人复核与图表均未实现，属于 Week4 正式实验范围。 |
| Architecture Freeze | `tests/unit/test_constants.py::test_constants_exist_with_expected_types`；`tests/unit/test_embedding.py::test_build_embedding_model_does_not_expose_model_name_parameter` | 部分覆盖 | 冻结常量和 Embedding 接口边界有自动化约束；“不新增技术栈/Agent/范围”属于变更治理规则，不能由现有单元测试完整证明。 |

## 未覆盖或部分覆盖的具体缺口

1. SQLite 职责隔离：未接入 SQLAlchemy，因此没有业务 SQLite 与 `SqliteSaver` 独立文件、ORM 不修改 Checkpoint 表的实现和自动化证明。这是 §13 指定的 Week3 边界，不应在本次验收任务中补做。
2. Checkpoint：缺少构造不兼容 `graph_version` 后确认返回 `CHECKPOINT_VERSION_MISMATCH` 的自动化测试。
3. SQLAlchemy：缺少业务库 Schema、repository、可重复初始化、业务事务不修改 Checkpoint 的实现与测试；按计划属于 Week3。
4. React + 基础 SSE：缺少 React 应用、当前连接实时进度、后端 event_id 对照和断线错误态测试；按计划属于 Week3。
5. interrupt UI：缺少三类 interrupt 的浏览器表单/弹窗、刷新恢复和重复提交幂等测试；按计划属于 Week3。
6. Git/GitHub：缺少可审查的 PR 模板/检查配置，未能自动验证“测试通过才合并”和分支保护规则。
7. 对比实验：仅完成 Case 1 最小脚本与 smoke test；缺少 Case 2 跨语言简历匹配、Case 3 多轮面试，缺少全量 18 条运行记录、人工评分 rubric、双人复核、可视化。Case 2/3 明确保留到 Week4，Case 1 结果不能代表整体架构收益。
8. Architecture Freeze：现有测试只能锁定部分常量/API；技术栈、Agent、范围没有被外部流程或策略自动强制，仍需依赖变更审查。

## 对比实验：方法论修正记录

本节保留 Task 3 已提交的原文：

> 改了什么：新增简单 Python 后端 JD 的 baseline 与 JD Worker 对比脚本、原生 SQLite 运行记录和不依赖真实 Provider 的 smoke test。
>
> 为什么改：满足架构文档 §11 与 Week2 门禁的最小实验要求，并记录正常、降级、失败运行的 Schema、证据、调用和估算 token 指标。
>
> 排除了什么方案：未引入 SQLAlchemy、React 或 Docker；Week1-2 门禁要求仅使用标准库 sqlite3，人工 rubric、双人复核和图表留给 Week4 正式实验。

## 对比实验：已知测量局限

本节保留 Task 3 已提交脚本中的原文：

> `unsupported_skill_claims: strict evidence substring matching; minor paraphrases can be false positives.`
>
> `Scope: both branches bypass Supervisor routing; LLM calls/tokens exclude production Supervisor overhead.`

Case 1 每个架构仅运行 `n=3`。在该样本量下，成功率和调用成本差异不构成统计显著结论。

`priority` 字段（`must`/`preferred`/`inferred`）判定可靠性未被当前实验指标覆盖；真正的架构差异点设计在 Case 2/3，留给 Week4。

## Week2 门禁判断

§14 Week2 的周门禁原文为：“Supervisor、3 个 Worker、State、HITL、错误恢复、日志和 Checkpoint 的全部核心验收通过。只有通过该门禁，才允许接入 SQLAlchemy 和前端。”

核心标记组 `171 passed, 73 deselected`，且全量 `244 passed`。本报告中 Supervisor、JD 解析/简历匹配/模拟面试三个 Worker、State、三类 HITL、JSON 重试耗尽节点级降级、日志与 Checkpoint 都有对应自动化用例并通过。因此，**就 §14 所列的核心 Agent 门禁而言，可以宣布通过**。

这里需要补一条范围说明：`73 deselected` 不是“全部 integration 测试”或“全部 Checkpoint 测试”，而是“未打 `core_agent_tests` 标记的其余测试”。本次 `--collect-only` 结果显示，`tests/integration/test_checkpoint_restart.py` 中作为 HITL/Checkpoint 核验依据的跨进程恢复用例实际已经包含在 `core_agent_tests` 的 171 条里，并不在被排除的 73 条之中。被排除的主要是未打该标记的 API 边界、部分 unit smoke test 和实验脚本 smoke test，因此本报告对 Checkpoint/HITL 的门禁判断可以同时由 `core_agent_tests` 与全量 `244 passed` 共同支撑，而不是只依赖全量测试兜底。

但 §14 的 Week2 工作项还写有“完成 Agent 核心验收套件和 3 个对比实验最小脚本”。当前只有 Case 1；Case 2/3 明确属于 Week4 正式实验范围。故本结论不表示 §11 的完整 3 Case 正式实验验收已经完成，也不允许将 Case 1 结果外推为完整架构收益。进入 Week3 的 SQLAlchemy/前端开发不受核心 Agent 门禁阻断；Week4 的完整实验验收仍为未完成事项。

## 仓库卫生

`git_log_full.txt` 已由 `.gitignore` 第 15 行规则忽略，当前不是未跟踪文件。建议继续保持忽略，不提交：该文件是本地 Git 历史导出物，可由 `git log` 可重复生成，不是运行、测试或交付所必需的源文件。当前实际未跟踪文件是 `data/experiments.sqlite3`；其是否提交应由后续任务根据是否作为可审计的真实实验结果决定，本次未擅自处理。

建议在 Week4 启动前决定是否将这份数据纳入版本控制或另行归档，避免 Case 1 的 baseline 数据在环境清理后丢失，影响后续 Week4 正式实验的历史对照。