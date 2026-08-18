from typing import Annotated, cast

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool, InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command
from pydantic import Field

from app.graph.state import WorkerName


HANDOFF_DESTINATION_METADATA = "__handoff_destination"


def create_semantic_handoff_tool(worker: WorkerName, description: str) -> BaseTool:
    tool_name = f"transfer_to_{worker}"

    @tool(tool_name, description=description)
    def handoff(
        confidence: Annotated[float, Field(ge=0, le=1, description="Semantic routing confidence")],
        reason: Annotated[str, Field(min_length=1, max_length=300, description="Brief semantic routing reason")],
        state: Annotated[dict, InjectedState],
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> Command:
        last_message = cast(AIMessage, state["messages"][-1])
        if len(last_message.tool_calls) != 1:
            raise ValueError("Supervisor must select exactly one worker")
        tool_message = ToolMessage(
            content=f"Routed to {worker}",
            name=tool_name,
            tool_call_id=tool_call_id,
            response_metadata={HANDOFF_DESTINATION_METADATA: worker},
        )
        return Command(
            goto=worker,
            graph=Command.PARENT,
            update={
                **state,
                "route_audit": {
                    "worker": worker,
                    "confidence": float(confidence),
                    "reason": reason,
                },
                "messages": [*state["messages"], tool_message],
            },
        )

    handoff.metadata = {HANDOFF_DESTINATION_METADATA: worker}
    return handoff
