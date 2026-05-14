from pydantic import BaseModel

from learning_agent.agents.base import AgentWrapper


class ResponseModelInterpretation(BaseModel):
    interpretation: str


class InterpretationWrapperAgent(AgentWrapper):
    def __init__(self):
        super().__init__(
            instructions=["Summarize the error messages into a single error message"],
            response_model=ResponseModelInterpretation,
            model="gpt-4o-mini",
            model_type="openai",
        )

    def interpret(self, question, code, result):
        PROMPT = (
            f"""The user's question is `{question}`. The code that was generated """
            f"""to answer the question is `{code}`. The result of executing this """
            f"""code is `{result}`. Interpret the result into a single sentence."""
        )
        response = self.run(PROMPT)
        return response.content.interpretation
