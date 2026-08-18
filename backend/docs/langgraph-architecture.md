# LangGraph Supervisor-Worker Architecture

## Runtime flow

```text
POST /threads/{id}/messages
  -> LangGraphRuntime
  -> Supervisor (route only)
  -> exactly one Worker
  -> Worker structured decision (respond or one bounded domain action)
  -> ConversationActionService (only when an action was selected)
  -> Supervisor post-model guard (FINISH only)
  -> Output Sanitizer
  -> Thread state + sanitized SSE event
```

The graph uses `langgraph-supervisor.create_supervisor`. Custom handoff tools require exactly one tool call and checkpoint the routing worker, confidence, and short reason. Routing audit data stays in the LangGraph checkpoint and is never included in SSE payloads.

## State ownership

- API-owned read-only input: `readonly_context`, `run_id`
- Supervisor-owned: `route_audit`, supervisor/tool messages
- Worker-owned: `worker_name`, `worker_result`, `visible_output`, worker message
- Output-owned: `public_output`

Workers return only their documented fields. `worker_result.action` is an internal structured command and never appears in SSE. `public_output` is the only graph text consumed by `LangGraphRuntime`; executed business actions are converted to final user fields and passed through the same output sanitizer before persistence.

## Worker boundaries

- `resume_worker`: resume structure, versions, experience, projects, and skill expression
- `jd_worker`: one JD's responsibilities, requirements, skills, and interview focus
- `match_worker`: resume-to-JD evidence, strengths, gaps, and priorities
- `interview_worker`: interview questions, answer evaluation, feedback, and review
- `chat_worker`: smalltalk, general career guidance, and insufficiently specified requests

Within those boundaries, Worker action contracts are also mutually exclusive:

- `jd_worker`: `respond` or `create_jd`
- `match_worker`: `respond` or `run_match`
- `interview_worker`: `respond`, `start_interview`, `submit_interview_answer`, `continue_interview`, or `end_interview`
- `resume_worker` and `chat_worker`: `respond` only

The Supervisor receives a compact read-only routing context containing selected-resource status and any active interview interrupt. This allows an answer to an active interview question to remain in the interview flow without API-layer heuristics. The selected Worker then makes the narrower respond-versus-action decision using validated structured output.

These boundaries are intentionally mutually exclusive. The API layer contains no keyword or regular-expression intent routing.

After a Worker writes `worker_name`, the Supervisor post-model hook replaces any second
handoff attempt with an idempotent `FINISH` message. This guard is based on graph state,
not user-text heuristics, so the first route remains entirely LLM-semantic while a Worker
cannot be invoked twice in the same run.

## Models and tracing

`LLM_MODE=stub` runs the complete graph and checkpoints but always routes to `chat_worker`; it does not inspect text or claim semantic understanding. `LLM_MODE=openai` requires `OPENAI_API_KEY` from the local `.env` and uses the configured OpenAI-compatible chat model for semantic routing and Worker output. Set `OPENAI_BASE_URL` for Alibaba DashScope or another compatible provider; leave it empty for the default OpenAI endpoint.

LangSmith tracing is controlled by `LANGSMITH_TRACING`, `LANGSMITH_PROJECT`, and the local `LANGSMITH_API_KEY` (or OAuth-managed environment). The application injects these settings into the process before graph construction. Do not place credentials in `.env.example`, logs, traces, plans, or commits.

Run the synthetic live route evaluation from the backend directory after local authentication:

```powershell
.\.venv\Scripts\python.exe -u .\scripts\evaluate_supervisor_routes.py
```

The evaluator prints only case IDs and selected Worker names. It currently covers eleven
cases, including context references, interview continuation, ambiguous requests, and
smalltalk.

## Recovery

SqliteSaver stores checkpoints in `GRAPH_CHECKPOINT_PATH`. On startup, `LangGraphRuntime` finds application threads left in `running`, resumes an incomplete graph checkpoint when possible, or starts the uncheckpointed request from its last user message. `active_run_id` prevents an older recovered run from overwriting a newer result. Before a domain action starts, `conversation_action` records its run and action; recovery reuses an already persisted match, JD job, or interview transition instead of invoking the model or applying the action twice.

## Structured business tasks

`TaskRuntime` keeps the HTTP/HITL workflow separate from Supervisor routing. In OpenAI-compatible mode it delegates to `LLMTaskService`, whose outputs are validated as one of these contracts before persistence:

- `StructuredResume`: profile, target role, education, skills, experience, projects, privacy flag, and chunk count
- `StructuredJD`: title, seniority, responsibilities, required/preferred/inferred skills, and interview focus
- `StructuredMatch`: bounded weighted dimensions, strengths, gaps, and traceable evidence
- `InterviewQuestion`, `InterviewFeedback`, and `InterviewReport`

Resume text is extracted locally from PDF, DOCX, or TXT and direct email/phone fields are replaced before model submission and vectorization. A structured result is persisted with status `parsed`, then deterministic chunks are embedded through the Alibaba native API and upserted into the local ChromaDB collection. The status becomes `indexed` only after the vector write succeeds. Pending jobs reuse an existing structured result after restart and continue indexing without calling the model again; an index failure preserves `parsed` data and fails the job with `RESUME_INDEX_FAILED`.

Real match runs first query the selected resume partition using the parsed JD requirements and responsibilities. Only the retrieved resume chunks are passed to `LLMTaskService.match` as evidence; a missing index fails explicitly instead of silently falling back to the full resume.
