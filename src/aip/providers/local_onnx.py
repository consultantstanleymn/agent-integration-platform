"""Local CPU embeddings through ONNX Runtime (fastembed). No hosted call is made.

The model files are downloaded once from the public model hub into the fastembed cache.
Install with: pip install -e ".[local-embed]"
"""

from typing import Any

from aip.providers.base import ProviderError

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
DIMENSION = 384


class LocalOnnxEmbedder:
    name = "local_onnx"
    dim = DIMENSION

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        self.model_name = model_name
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            try:
                from fastembed import TextEmbedding
            except ImportError as exc:
                raise ProviderError("fastembed is not installed: pip install -e '.[local-embed]'") from exc
            self._model = TextEmbedding(model_name=self.model_name)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(x) for x in vector] for vector in self._load().embed(texts)]
