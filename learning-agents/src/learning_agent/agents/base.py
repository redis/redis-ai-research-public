from abc import ABC

from agno.agent import Agent
from agno.models.google import Gemini
from agno.models.openai import OpenAIChat
from pydantic import BaseModel


class AgentWrapper(Agent, ABC):
    def __init__(
        self,
        instructions: list[str],
        response_model: BaseModel,
        model: str = "gpt-4o-mini",
        model_type: str = "openai",
    ):
        self.agent = Agent(
            name="Column summary agent",
            model=model_type == "openai" and OpenAIChat(id=model) or Gemini(id=model),
            tools=[],
            instructions=instructions,
            description="",
            show_tool_calls=True,
            response_model=response_model,
            debug_mode=False,
        )

    def run(self, *args, **kwargs):
        return self.agent.run(*args, **kwargs)
