
from pydantic import BaseModel, Field
from rich.console import Console
from rich.prompt import Prompt

from learning_agent.agents.base import AgentWrapper
from learning_agent.agents.guidance import GuidanceAgent
from learning_agent.core.utils import (print_code, print_header, print_panel,
                                       print_panel_text)
from learning_agent.logging.logger import log_event, timed_event_context

console = Console()


def execute_code(code, df) -> tuple[dict, dict]:
    locals_dict = {}
    print_panel_text("Executing code...", "Executing code")

    try:
        exec(code, {"df": df}, locals_dict)
        return locals_dict, {"status": 0, "error_message": None}
    except Exception as e:
        print_panel_text(f"Error executing code: {e}", "Error executing code")
        return locals_dict, {"status": -2, "error_message": e}


class FilterCode(BaseModel):
    """Response model for the filter agent containing the generated code.

    Attributes:
        code: The Pandas/Numpy code generated to answer the user's question.
    """

    code: str = Field(
        ..., description="Return the Pandas code needed to answer the question."
    )


class FilterAgentWrapper(AgentWrapper):
    """A wrapper for the filter agent that processes natural language queries.

    This agent interprets natural language questions about dataframes and generates
    appropriate Pandas/Numpy code to answer them. It supports interactive feedback
    to improve the generated code.
    """

    def __init__(self, guidance_agent: GuidanceAgent):
        """Initialize the FilterAgentWrapper with the filter agent."""
        self._init_non_reasoning()
        self.guidance_agent = guidance_agent

    def _init_reasoning(self):
        super().__init__(
            instructions=[
                "Given a sample of a pandas dataframe, write Pandas code to answer the question.",
            ],
            response_model=FilterCode,
            model="gpt-4o",
            model_type="openai",
        )
        self.agent.reasoning = True
        self.reasoning = True

    def _init_non_reasoning(self):
        super().__init__(
            instructions=[
                "Given a sample of a pandas dataframe, write Pandas code to answer the question.",
            ],
            response_model=FilterCode,
            model="gpt-4o-mini",
            model_type="openai",
        )
        self.agent.reasoning = False
        self.reasoning = False

    def construct_prefixed_question(
        self,
        question: str,
        df,
        error_message,
        error_history,
        guidance_message,
        retry_count: int,
        column_summary_dict,
        similar_matches,
    ):
        """
        Construct a prefixed question for the filter agent.

        Args:
            question (str): The natural language question to process.
            df (pd.DataFrame): The pandas dataframe to operate on.
            error_message (ErrorMessage): Optional error message from previous attempts.
            error_history (list[ErrorMessage]): History of error messages encountered.
            guidance_message (str): Guidance message from the guidance agent.
            retry_count (int): Number of retries attempted.
            column_summary_dict (dict): Summary of columns in the dataframe.

        Returns:
            str: The prefixed question to be used by the filter agent.
        """
        prefixed_question = f"You are given a dataframe named df. Here is a sample of from dataframe df: {df.head(n=2).to_dict()}, write Pandas/Numpy code to answer the user's question `{question}`. Return the answer in the variable `result`."

        if error_message is not None:
            prefixed_question += (
                f" In the prior attempt this action was taken: `{error_message.assistant_action}` "
                f"which resulted in this error message: `{error_message.user_feedback}`."
            )
            if retry_count >= 2:
                error_summaries = ";".join(
                    f"- Attempt: `{em.assistant_action}` → Error: `{em.user_feedback}`"
                    for em in error_history
                )
                prefixed_question += f" Here are the past failed attempts with the actions taken and corresponding messages: {error_summaries}."
            if retry_count >= 3:
                column_summary_str = "; ".join(
                    f"{col}: {desc}" for col, desc in column_summary_dict.items()
                )
                prefixed_question += f" Here is a summary of the columns in the dataframe: {column_summary_str}."

            if retry_count >= 4:
                self._init_reasoning()
                prefixed_question += " Arrive at a solution by reasoning step-by-step. Reflect on why the last code failed. Then, plan a corrected approach before generating code."

            prefixed_question += (
                f" Please try to fix the error and provide a new code snippet."
            )

        if guidance_message is not None:
            prefixed_question += f" {guidance_message}"

        if similar_matches:
            similar_str = [
                {"question": h["question"], "code": h["code"]} for h in similar_matches
            ]
            prefixed_question += f" Here are some similar questions and successful queries from past attempts: `{similar_str}`, please use these to guide your query creation if it is relevant to the user's question."

        return prefixed_question

    def run(self, *args, **kwargs):
        """Run the filter agent to process a natural language query.

        Args:
            *args: Variable length argument list containing:
                - question (str): The natural language question to process
                - df (pd.DataFrame): The pandas dataframe to operate on
            **kwargs: Arbitrary keyword arguments including:
                - error_history (list[ErrorMessage]): History of error messages encountered
                - retry_count (int): Number of retries attempted
                - column_summary_dict (dict): Summary of columns in the dataframe
                - similar_matches (list or None): List of similar past matches, if any

        Returns:
            A tuple containing:
                - A dictionary of local variables after code execution
                - The generated code string
                - A status dictionary with keys "status" and "error_message"
                - A metrics dictionary with execution metadata
        """
        # print(args)
        # print(len(args))
        # print(kwargs)

        df = args[1]
        self.agent.reasoning = False
        # Error handling
        error_message = kwargs["error_message"]
        similar_matches = kwargs["similar_matches"]
        if error_message is not None:
            log_event(
                "filter",
                "error_message",
                {"error_message": error_message.model_dump()},
                success=True,
            )
        error_history = kwargs["error_history"]
        retry_count = kwargs["retry_count"]
        column_summary_dict = kwargs["column_summary_dict"]
        print_header(f"Running filter agent -- Attempt {retry_count}")
        # print(f"[orange]error_history: {error_history}[/orange]")
        # print(f"[pink]column_summary_dict: {column_summary_dict}[/pink]")
        guidance_message = self.guidance_agent.get_guidance(args[0])
        # log_event(
        #    "filter",
        #    "guidance_message",
        #    {"guidance_message": guidance_message},
        #    success=True,
        # )
        if guidance_message is not "":
            print_panel_text(guidance_message, "Guidance Message")

        prefixed_question = self.construct_prefixed_question(
            question=args[0],
            df=df,
            error_message=error_message,
            error_history=error_history,
            guidance_message=guidance_message,
            retry_count=retry_count,
            column_summary_dict=column_summary_dict,
            similar_matches=similar_matches,
        )
        print_panel_text(prefixed_question, "Prefixed Question")

        # log_event(
        #    "filter",
        #    "prefixed_question",
        #    {"prefixed_question": prefixed_question},
        #    success=True,
        # )

        args_with_prefix = (prefixed_question,)

        with timed_event_context("llm_call") as log_context:
            response = super().run(*args_with_prefix, **kwargs)

        # print(f"[green]Q: {args[0]}[/green]")
        # print(response.reasoning_content)
        print_code(response.content.code)

        metrics = {
            "total_tokens": response.metrics.get("total_tokens"),
            "time": response.metrics.get("time"),
            "retry_count": retry_count,
        }
        print_panel(metrics, "Metrics")

        print_panel_text(
            "\nExecute: enter\nRetry: r\n",
            "⚙️ Confirm Action",
            "steel_blue",
            "steel_blue",
        )
        confirm = Prompt.ask("Your choice", choices=["", "r"], default="")

        if confirm == "r":
            return (
                {},
                response.content.code,
                {"status": -1, "error_message": "User requested to retry"},
                metrics,
            )
        else:
            locals_dict, status_dict = execute_code(response.content.code, df)

            if self.reasoning:
                self._init_non_reasoning()

            return (
                locals_dict,
                response.content.code,
                status_dict,
                metrics,
            )
