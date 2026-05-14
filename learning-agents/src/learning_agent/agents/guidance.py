import uuid
from typing import List

# Third‑party
from pydantic import BaseModel, Field

# Local imports (unchanged)
from learning_agent.agents.base import AgentWrapper
from learning_agent.core.redis_vector_index import RedisVectorIndex
from learning_agent.errors import ErrorMessage, SuccessMessage
from learning_agent.logging.logger import log_event_decorator


class ErrorGuidanceModel(BaseModel):
    """Model for storing error message guidance."""

    memory: str = Field(description="The memory note summarizing the error messages.")


ERROR_MESSAGE_GUIDANCE_PROMPT = (
    "For the user question `{success_message.question}`, "
    "the successful action was `{success_message.action}`. "
    "However the following actions resulted in the error messages listed: `{error_messages}` "
    "Create a memory note that can be stored and retrieved later for subsequent execution in order to not make these errors again. "
    "Return it in the JSON format {{'memory': <memory>}}"
)


class GuidanceAgent(AgentWrapper):
    """Agent wrapper that persists & retrieves guidance using RedisVL."""

    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self.redis_url = redis_url
        self.guidance: dict[str, str] = {}
        self._index_name = "guidance_docs"
        self._prefix = "guidance"

        # Set‑up Redis index
        self._init_index()
        super().__init__(
            instructions=[],
            response_model=ErrorGuidanceModel,
            model="gpt-4o-mini",
            model_type="openai",
        )

    def _init_index(self):
        self.index = RedisVectorIndex(
            col_query="question",
            col_response="guidance",
            index_name=self._index_name,
            prefix=self._prefix,
            redis_url=self.redis_url,
        )

    def _get_error_message_guidance_prompt(
        self, error_messages: List[ErrorMessage], success_message: SuccessMessage
    ):
        return ERROR_MESSAGE_GUIDANCE_PROMPT.format(
            error_messages=error_messages, success_message=success_message
        )

    def _get_error_guidance(
        self, error_messages: List[ErrorMessage], success_message: SuccessMessage
    ):
        prompt = self._get_error_message_guidance_prompt(
            error_messages, success_message
        )
        response = self.run(prompt)
        return response.content.memory

    @log_event_decorator()
    def add_guidance(
        self, error_messages: List[ErrorMessage], success_message: SuccessMessage
    ):
        """Insert or update guidance in RedisVL."""
        # Check for existing question in RedisVL
        embedding = RedisVectorIndex._embed(success_message.question)
        existing = self.index._vector_search_by_vector(embedding, k=1)
        if (
            existing
            and existing[0]["question"] == success_message.question
            and len(error_messages) == 0
        ):
            return f"Guidance already exists for question `{success_message.question}`"

        # Generate the (additional) guidance chunk via LLM
        error_guidance = self._get_error_guidance(error_messages, success_message)

        if existing and len(error_messages) > 0:
            error_guidance = f"{existing[0]['guidance']}, {error_guidance}"
            doc_id = existing[0]["id"]
        else:
            doc_id = f"{self._prefix}:{uuid.uuid4()}"

        # Upsert new/extended document

        self.index.load(
            [
                {
                    "id": doc_id,
                    "question": success_message.question,
                    "guidance": error_guidance,
                    "question_embedding": embedding.tobytes(),
                }
            ]
        )

        return f"Successfully added guidance `{error_guidance}` for question `{success_message.question}`"

    def get_guidance(self, question: str):
        """Retrieve nearest guidance for *question* from RedisVL."""
        results = self.index._vector_search_by_text(question, k=1)
        if results:
            return results[0]["guidance"]
        return ""


def _smoke_test(agent: GuidanceAgent):
    """Insert demo data and print three nearest matches."""
    sample = [
        (
            "How do I reset my password?",
            "Use the *Forgot Password* link on the login page.",
        ),
        ("How to change account email?", "Navigate to Settings → Account → Email."),
        (
            "Why am I seeing a 403 error?",
            "Ensure you have the correct API token attached.",
        ),
        ("I forgot my password", "Use the *Forgot Password* link on the login page."),
        ("I have a new email address", "Navigate to Settings → Account → Email."),
    ]
    # Bulk load
    payload = []
    for q, g in sample:
        payload.append(
            {
                "id": f"{agent._prefix}:{uuid.uuid4()}",
                "question": q,
                "guidance": g,
                "question_embedding": RedisVectorIndex._embed(q).tobytes(),
            }
        )
    agent.index.load(payload)

    # Query
    query = "resetting password"
    hits = agent.index._vector_search_by_text(query, k=3)
    print("Top‑3 matches for:", query)
    for hit in hits:
        print({"question": hit["question"], "guidance": hit["guidance"]})


if __name__ == "__main__":
    agent = GuidanceAgent()
    _smoke_test(agent)
