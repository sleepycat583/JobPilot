from dataclasses import dataclass
from typing import Any, Literal, Sequence
from uuid import uuid4

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI

from app.core.config import Settings
from app.graph.state import WorkerName


WORKER_NAMES: tuple[WorkerName, ...] = (
    "resume_worker",
    "jd_worker",
    "match_worker",
    "interview_worker",
    "chat_worker",
)


class StubSupervisorModel(BaseChatModel):
    """Non-semantic local fallback. It deliberately never inspects user text."""

    route: WorkerName = "chat_worker"
    available_tools: tuple[str, ...] = ()

    @property
    def _llm_type(self) -> str:
        return "career-supervisor-stub"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"route": self.route}

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Any | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> "StubSupervisorModel":
        del tool_choice, kwargs
        names = tuple(tool.name for tool in tools if isinstance(tool, BaseTool))
        return self.model_copy(update={"available_tools": names})

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager, kwargs
        latest_human_index = max(
            (index for index, message in enumerate(messages) if isinstance(message, HumanMessage)),
            default=-1,
        )
        worker_has_returned = any(
            isinstance(message, AIMessage) and message.name in WORKER_NAMES
            for message in messages[latest_human_index + 1 :]
        )
        if worker_has_returned:
            message = AIMessage(content="FINISH", name="supervisor")
        else:
            tool_name = f"transfer_to_{self.route}"
            if self.available_tools and tool_name not in self.available_tools:
                raise RuntimeError(f"Supervisor handoff tool is missing: {tool_name}")
            message = AIMessage(
                content="",
                name="supervisor",
                tool_calls=[
                    {
                        "name": tool_name,
                        "args": {
                            "confidence": 1.0,
                            "reason": "Stub mode performs no semantic inference and uses the configured test route.",
                        },
                        "id": str(uuid4()),
                        "type": "tool_call",
                    }
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=message)])


@dataclass(frozen=True)
class ModelBundle:
    mode: Literal["stub", "openai"]
    supervisor_model: BaseChatModel
    worker_model: BaseChatModel | None


def build_model_bundle(settings: Settings) -> ModelBundle:
    if settings.llm_mode == "stub":
        return ModelBundle(mode="stub", supervisor_model=StubSupervisorModel(), worker_model=None)
    if settings.openai_api_key is None or not settings.openai_api_key.get_secret_value().strip():
        raise RuntimeError("LLM_MODE=openai requires OPENAI_API_KEY in the environment")
    model_kwargs: dict[str, Any] = {
        "model": settings.openai_model,
        "api_key": settings.openai_api_key,
        "temperature": 0,
        "timeout": 30,
        "max_retries": 2,
    }
    if settings.openai_base_url and settings.openai_base_url.strip():
        model_kwargs["base_url"] = settings.openai_base_url.strip()
    model = ChatOpenAI(
        **model_kwargs,
    )
    return ModelBundle(mode="openai", supervisor_model=model, worker_model=model)
