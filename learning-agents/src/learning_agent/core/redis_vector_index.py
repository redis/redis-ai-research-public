from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np
import openai
from redisvl.index import SearchIndex
from redisvl.query import TextQuery, VectorQuery
from redisvl.schema import IndexSchema

openai.api_key = os.getenv("OPENAI_API_KEY")  # must be set
if os.getenv("OPENAI_API_BASE"):
    openai.base_url = os.getenv("OPENAI_API_BASE")

EMBED_MODEL = "text-embedding-3-small"  # 1536‑dimensional vectors
EMBED_DIM = 1536


@dataclass
class RedisVectorIndex:
    """Create (or reuse) a RedisVL vector search index.

    Parameters
    ----------
    col_query : str
        Name of the text column whose *embedding* is built and searched.
    col_response : str
        Name of the text column returned as the *answer* or payload.
    index_name : str
        RediSearch index name.
    prefix : str
        Document key prefix (e.g. "guidance").
    dims : int
        Dimensionality of the embedding vectors.
    redis_url : str, default "redis://localhost:6379"
        Connection URL for Redis(‑Stack).
    """

    col_query: str
    col_response: str
    index_name: str
    prefix: str
    redis_url: str = "redis://localhost:6379"
    additional_fields: list[dict] = field(default_factory=list)

    def __post_init__(self):
        # Build schema once and ensure index exists
        schema_dict = {
            "index": {"name": self.index_name, "prefix": self.prefix},
            "fields": [
                {"name": self.col_query, "type": "text"},
                {"name": self.col_response, "type": "text"},
                {
                    "name": f"{self.col_query}_embedding",
                    "type": "vector",
                    "attrs": {
                        "dims": EMBED_DIM,
                        "distance_metric": "COSINE",
                        "algorithm": "HNSW",
                        "datatype": "FLOAT32",
                    },
                },
                *self.additional_fields,
            ],
        }
        schema = IndexSchema.from_dict(schema_dict)
        self.index: SearchIndex = SearchIndex(schema, redis_url=self.redis_url)
        if not self.index.exists():
            self.index.create(overwrite=False)

    def _full_text_search(self, query_text: str, k: int = 3):
        query = TextQuery(
            text=query_text,
            text_field_name=self.col_query,
        )
        return self.index.search(query, num_results=k)

    def _vector_search_by_vector(
        self, query_vector: np.ndarray, k: int = 3, threshold: float = 0.0
    ):
        vq = VectorQuery(
            vector_field_name=f"{self.col_query}_embedding",
            vector=query_vector.tobytes(),
            return_fields=["id", self.col_query, self.col_response, "vector_distance"]
            + [f["name"] for f in self.additional_fields],
            num_results=k,
            return_score=True,
        )
        response = self.index.query(vq)
        threshold = 1 - threshold
        return [hit for hit in response if float(hit["vector_distance"]) <= threshold]

    def _vector_search_by_text(
        self, query_text: str, k: int = 3, threshold: float = 0.5
    ):
        vec = self._embed(query_text)
        return self._vector_search_by_vector(vec, k, threshold)

    @staticmethod
    def _embed(text: str) -> np.ndarray:
        response = openai.embeddings.create(model=EMBED_MODEL, input=[text])
        vec = response.data[0].embedding
        return np.array(vec, dtype=np.float32)

    def load(self, docs):
        self.index.load(docs)

    def query(self, vector_query):
        return self.index.query(vector_query)
