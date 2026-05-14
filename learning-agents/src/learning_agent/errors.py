from pydantic import BaseModel, Field


class ErrorMessage(BaseModel):
    """Model for storing error messages and feedback in the filter agent.

    Attributes:
        user_input: The original user input that caused the error.
        assistant_action: The action taken by the assistant that resulted in the error.
        user_feedback: Feedback provided by the user about the error.
    """

    user_input: str = Field(description="The user input.")
    assistant_action: str = Field(description="The action the assistant took.")
    user_feedback: str = Field(description="The feedback to the filter agent.")


class SuccessMessage(BaseModel):
    """Model for storing success messages."""

    question: str = Field(description="User question.")
    action: str = Field(description="Action that resulted in success")
