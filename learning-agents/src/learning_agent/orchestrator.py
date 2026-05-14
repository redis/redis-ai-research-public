import time
import uuid

import pandas as pd
from rich import print
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from learning_agent.agents.column_summary import ColumnSummaryAgentWrapper
from learning_agent.agents.filter import FilterAgentWrapper
from learning_agent.agents.guidance import GuidanceAgent
from learning_agent.core.cache import redis_cache_method
from learning_agent.core.redis_vector_index import RedisVectorIndex
from learning_agent.core.utils import print_code, print_panel, print_panel_text
from learning_agent.data.reader import RawDataReaderProcessor
from learning_agent.errors import ErrorMessage, SuccessMessage
from learning_agent.logging.logger import log_event, log_event_decorator

THRESHOLD_EXACT_MATCH = 0.001
THRESHOLD_SIMILAR_MATCH = 0.2
TOP_K = 2

console = Console()


def execute_code(code, df) -> tuple[dict, dict]:
    locals_dict = {}
    print_code(code, "Cached Code to be executed")
    print_panel_text(
        "\nExecute: enter\nReject: r\n", "⚙️ Confirm Action", "steel_blue", "steel_blue"
    )
    confirm = Prompt.ask("Your choice", choices=["", "r"], default="")
    if confirm == "":
        try:
            exec(code, {"df": df}, locals_dict)
            return locals_dict, {"status": 0, "error_message": None}
        except Exception as e:
            print_panel_text(f"Error executing code: {e}", "Error executing code")
            return locals_dict, {"status": -2, "error_message": e}
    else:
        return locals_dict, {
            "status": -1,
            "error_message": "User rejected execution of cached code",
        }


class AgentOrchestrator:
    """A planner agent that processes and analyzes pandas dataframes using various specialized agents.

    This agent coordinates the work of multiple specialized agents (filter, column summary, topic extractor)
    to analyze and process pandas dataframes. It maintains state about the dataframe and provides
    methods for data analysis and manipulation.
    """

    @log_event_decorator(log_payload=False)
    def __init__(
        self, filename="data", delimiter=None, guidance_agent: GuidanceAgent = None
    ):
        """Initialize the PandasPlannerAgent.

        Args:
            filename: The name of the file containing the data to process.
        """
        self.error_messages: list[ErrorMessage] = []
        self.success_message: SuccessMessage = None
        self.column_summary_dict = {}
        self.df = self.get_data(filename, delimiter)
        self.column_summary_dict, self.stats = self.get_summary()
        self.filter_agent_wrapper = FilterAgentWrapper(guidance_agent)
        self._success_index = RedisVectorIndex(
            col_query="question",
            col_response="result",
            index_name="success_docs",
            prefix="success",
            redis_url=guidance_agent.redis_url,
            additional_fields=[
                {"name": "freq", "type": "numeric"},
                {"name": "ts", "type": "numeric"},
                {"name": "code", "type": "text"},
            ],
        )

    @redis_cache_method(ttl=20)
    def get_data(self, filename, delimiter=None) -> tuple[dict, pd.DataFrame]:
        self.filename = filename
        df = RawDataReaderProcessor(filename, delimiter).get_df()
        return df

    @redis_cache_method(ttl=20)
    def get_summary(self) -> dict:
        column_summary_dict = self.get_column_summary()
        stats = self.df.describe()
        return column_summary_dict, stats

    def get_column_summary(self) -> dict:
        """Get a summary of each column in the dataframe using the column summary agent.

        Returns:
            self: For method chaining.
        """
        column_name, column_summary = ColumnSummaryAgentWrapper().run(self.df)
        return dict(zip(column_name, column_summary))

    @redis_cache_method(ttl=20)
    def filter_data(self, question):
        """Filter and process the dataframe based on a natural language question.

        Uses the filter agent to interpret the question and apply appropriate transformations
        to the dataframe. Supports interactive feedback to improve results.

        Args:
            question: A natural language question describing the desired data processing.

        Returns:
            The result of the data processing operation.
        """
        t0 = time.time()
        embedding = RedisVectorIndex._embed(question)
        hits = self._success_index._vector_search_by_vector(embedding, k=TOP_K)

        done, error_message, self.success_message = False, None, None
        self.error_messages, metrics_list = [], []
        cached = None
        similars = None
        if hits:
            exacts = [
                h
                for h in hits
                if h["question"] == question
                or float(h["vector_distance"]) < THRESHOLD_EXACT_MATCH
            ]
            similars = [
                h for h in hits if float(h["vector_distance"]) < THRESHOLD_SIMILAR_MATCH
            ]
            if exacts:
                # get the most recent cached result
                cached = max(exacts, key=lambda x: x["ts"])
                print_panel(cached, "Obtained exact match from Execution Cache")
            elif similars:
                similars = sorted(similars, key=lambda x: x["vector_distance"])[
                    :2
                ]  # Take the top 2 most similar (lowest distance)
                print_panel(similars, "Similar matches found")

        if cached:
            cached["freq"] = int(cached.get("freq", 0)) + 1
            cached["ts"] = int(time.time())
            self._success_index.load(
                [
                    {
                        "id": cached["id"],
                        "question": cached["question"],
                        "result": cached["result"],
                        "code": cached["code"],
                        "freq": cached["freq"],
                        "ts": cached["ts"],
                        "question_embedding": embedding.tobytes(),
                    }
                ]
            )
            metrics = {
                "total_tokens": 0,
                "time": time.time() - t0,
            }
            self.success_message = SuccessMessage(
                question=question,
                action=cached["code"],
            )
            # TODO: Needs to be executed and returned as a result

            locals_dict, metrics_dict = execute_code(cached["code"], self.df)
            if metrics_dict.get("status") == -2:
                print_panel_text(
                    f"Execution resulted in the following error: {metrics_dict.get('error_message')}. \n Continuing with regular execution...",
                    "Error executing code",
                )
                error_message = ErrorMessage(
                    user_input=question,
                    assistant_action=cached["code"],
                    user_feedback=metrics_dict.get("error_message"),
                )
                self.error_messages.append(error_message)
            elif metrics_dict.get("status") == -1:
                print_panel_text(
                    "User rejected execution of cached code. Continuing with regular execution...",
                    "User rejected execution of cached code",
                )
                error_message = ErrorMessage(
                    user_input=question,
                    assistant_action=cached["code"],
                    user_feedback="User rejected execution of cached code",
                )
                self.error_messages.append(error_message)
            else:
                return locals_dict.get("result", None), metrics

        retry_count = 0
        while not done:
            run_response, code, status_dict, metrics = self.filter_agent_wrapper.run(
                question,
                self.df,
                error_message=error_message,
                error_history=self.error_messages,
                retry_count=retry_count,
                column_summary_dict=self.column_summary_dict,
                similar_matches=similars,
            )
            metrics_list.append(metrics)
            if status_dict.get("status") == -1:
                retry_count += 1
                print("\n")
                console.print(
                    Panel.fit(
                        "[bold steel_blue]:sparkles: Tell me how I can improve! :sparkles:\n",
                        border_style="steel_blue",
                        title="Feedback Request",
                        title_align="left",
                    )
                )
                input_value = Prompt.ask(
                    "[steel_blue bold]> Your input[/steel_blue bold]"
                )

                if input_value != "":
                    done = False
                    error_message = ErrorMessage(
                        user_input=question,
                        assistant_action=code,
                        user_feedback=input_value,
                    )
                    log_event(
                        "planner",
                        "error_message",
                        {
                            "error_message": error_message.model_dump(),
                            "retry_count": retry_count,
                        },
                        success=False,
                    )
                    self.error_messages.append(error_message)
                else:
                    done = True
                    return "Exited with no satisfactory result", metrics_list
            elif status_dict.get("status") == -2:
                done = False
                custom_error_feedback = f"Execution resulted in the following error: {status_dict.get('error_message')}"
                error_message = ErrorMessage(
                    user_input=question,
                    assistant_action=code,
                    user_feedback=custom_error_feedback,
                )
                log_event(
                    "planner",
                    "error_message",
                    {
                        "error_message": error_message.model_dump(),
                        "retry_count": retry_count,
                    },
                    success=False,
                )
                self.error_messages.append(error_message)
            else:
                self._success_index.load(
                    [
                        {
                            "id": f"success:{uuid.uuid4()}",
                            "question": question,
                            "result": str(run_response.get("result", None)),
                            "code": str(code),
                            "freq": 1,
                            "ts": int(time.time()),
                            "question_embedding": embedding.tobytes(),
                        }
                    ]
                )
                done = True
                self.success_message = SuccessMessage(
                    question=question,
                    action=code,
                )
                return run_response.get("result", None), metrics_list
            retry_count += 1
