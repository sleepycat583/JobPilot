from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph_supervisor import create_supervisor

from app.graph.handoff import create_semantic_handoff_tool
from app.graph.models import ModelBundle
from app.graph.prompts import build_supervisor_prompt
from app.graph.state import CareerGraphState, WorkerName
from app.graph.workers import build_worker_graph, finalize_supervisor_step


WORKER_DESCRIPTIONS: dict[WorkerName, str] = {
    "resume_worker": "Route only resume structure, resume versions, experience, projects, or skill-expression requests.",
    "jd_worker": "Route only requests about parsing one job description and its requirements.",
    "match_worker": "Route only resume-to-JD comparison, evidence, strengths, gaps, and match analysis.",
    "interview_worker": "Route only mock interview questions, answer evaluation, feedback, and review.",
    "chat_worker": "Route smalltalk, general career advice, and requests that lack enough information for another worker.",
}


def build_career_graph(models: ModelBundle, checkpointer: BaseCheckpointSaver):
    workers = [build_worker_graph(name, models) for name in WORKER_DESCRIPTIONS]
    handoff_tools = [
        create_semantic_handoff_tool(name, description)
        for name, description in WORKER_DESCRIPTIONS.items()
    ]
    return create_supervisor(
        workers,
        model=models.supervisor_model,
        tools=handoff_tools,
        prompt=build_supervisor_prompt,
        post_model_hook=finalize_supervisor_step,
        state_schema=CareerGraphState,
        output_mode="last_message",
        add_handoff_messages=True,
        add_handoff_back_messages=True,
        parallel_tool_calls=False,
        supervisor_name="supervisor",
    ).compile(checkpointer=checkpointer, name="career_agent_graph")
