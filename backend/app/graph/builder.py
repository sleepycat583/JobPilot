from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph_supervisor import create_supervisor

from app.graph.handoff import create_semantic_handoff_tool
from app.graph.models import ModelBundle
from app.graph.prompts import build_supervisor_prompt
from app.graph.state import CareerGraphState, WorkerName
from app.graph.workers import build_worker_graph, finalize_supervisor_step


WORKER_DESCRIPTIONS: dict[WorkerName, str] = {
    "resume_worker": "Route only operations on the user's resume content, structure, versions, experience, projects, or skill expression. Generic career advice about preparing a resume, with no request to change an existing resume, belongs to chat. If a concrete resume operation is clear but the file is missing, still route here.",
    "jd_worker": "Route only the goal to parse or explain one job description's responsibilities, requirements, skills, or interview focus. If the JD is missing, still route here when that goal is clear.",
    "match_worker": "Route only the goal to compare a resume with a job description, explain evidence, strengths, gaps, score, or priorities. If materials are missing, still route here when comparison is explicit.",
    "interview_worker": "Route only the goal to start, continue, answer, skip, evaluate, or review a mock interview. An explicit switch away from a completed or ended interview takes precedence.",
    "chat_worker": "Route smalltalk, general career advice, or messages whose actual goal cannot be assigned to one of the four domain workers. Do not use chat merely because a clearly requested domain task lacks a resource.",
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
