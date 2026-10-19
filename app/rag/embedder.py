"""[R 단계 준비 3] 텍스트 → 벡터. 백엔드를 교체할 수 있게 인터페이스로 분리."""
from typing import Protocol

from app import usage as usage_log
from app.config import Settings


class Embedder(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class OpenAIEmbedder:
    def __init__(self, api_key: str, model: str):
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def _embed(self, texts: list[str], feature: str) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), 100):  # 배치
            with usage_log.timer() as elapsed:
                res = self.client.embeddings.create(model=self.model, input=texts[i:i + 100])
            usage_log.record(usage_log.Usage(
                feature=feature, model=self.model, kind="embedding",
                input_tokens=getattr(res.usage, "prompt_tokens", 0) or 0, latency_ms=elapsed(),
            ))
            out.extend(d.embedding for d in res.data)
        return out

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, "embed_index")

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "embed_query")[0]


class LocalEmbedder:
    """multilingual-e5 계열은 'query: ' / 'passage: ' 접두어를 붙여야 성능이 나온다."""

    def __init__(self, model: str):
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(model)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.model.encode([f"passage: {t}" for t in texts], normalize_embeddings=True).tolist()

    def embed_query(self, text: str) -> list[float]:
        return self.model.encode(f"query: {text}", normalize_embeddings=True).tolist()


def build_embedder(s: Settings) -> Embedder:
    if s.embedding_backend == "local":
        return LocalEmbedder(s.local_embedding_model)
    return OpenAIEmbedder(s.openai_api_key, s.openai_embedding_model)
