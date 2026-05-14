from pydantic import BaseModel, Field

from learning_agent.agents.base import AgentWrapper


class ColumnSummaryResponse(BaseModel):
    """Response model for the column summary agent.

    Attributes:
        column_name: A list of column names from the dataframe.
        column_summary: A list of summaries corresponding to each column.
    """

    column_name: list[str] = Field(description="A list of column names.")
    column_summary: list[str] = Field(
        description="A list of summaries for the columns."
    )


class ColumnSummaryAgentWrapper(AgentWrapper):
    """A wrapper for the column summary agent that analyzes dataframe columns.

    This agent examines the structure and content of dataframe columns and provides
    meaningful summaries of their contents and purpose.
    """

    def __init__(self):
        """Initialize the ColumnSummaryAgentWrapper with the column summary agent."""
        super().__init__(
            instructions=[
                "You are a helpful assistant that summarizes the columns of a pandas dataframe.",
            ],
            response_model=ColumnSummaryResponse,
            model="gpt-4o-mini",
            model_type="openai",
        )

    def run(self, *args, **kwargs):
        """Run the column summary process on the input dataframe.

        Args:
            *args: Variable length argument list containing:
                - df: The pandas dataframe to analyze
            **kwargs: Arbitrary keyword arguments passed to the agent.

        Returns:
            A tuple containing:
                - A list of column names
                - A list of corresponding column summaries
        """
        df = args[0]
        prefixed_question = f"Given a sample of a pandas dataframe named df: {df.head(n=2).to_dict()}, can you summarize the columns in the dataframe?"
        args_with_prefix = (prefixed_question,)
        response = super().run(*args_with_prefix, **kwargs)
        return response.content.column_name, response.content.column_summary
