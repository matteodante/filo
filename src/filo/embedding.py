"""Qwen3-Embedding-0.6B on ONNX Runtime, without torch.

The ONNX export holds only the transformer. Everything sentence-transformers does around it lives
here: the query instruction prefix, last-token pooling, and L2 normalization.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import onnxruntime
from pydantic_ai.embeddings import EmbeddingModel, EmbeddingResult, EmbeddingSettings
from pydantic_ai.embeddings.result import EmbedInputType
from tokenizers import Tokenizer

# Qdrant's export of Qwen/Qwen3-Embedding-0.6B (Apache-2.0): fp32 weights, reported identical to the
# sentence-transformers model, with the upstream tokenizer.json byte for byte.
EMBEDDING_REPO = "Qdrant/Qwen3-Embedding-0.6B-onnx"
EMBEDDING_REVISION = "af95f2c416ffe9379369ad64f9113e865db6112c"
EMBEDDING_FILES = ("onnx/model.onnx", "onnx/model.onnx_data", "tokenizer.json")
# The "query" prompt from the upstream config_sentence_transformers.json; documents get none.
QUERY_PROMPT = (
    "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery:"
)
MAX_INPUT_TOKENS = 32_768
BATCH_SIZE = 8


class OnnxQwen3EmbeddingModel(EmbeddingModel):
    """Run the pinned export from a local snapshot directory, loading it on first use."""

    def __init__(self, snapshot: Path) -> None:
        super().__init__()
        self._snapshot = snapshot
        self._lock = threading.Lock()
        self._session: onnxruntime.InferenceSession | None = None
        self._tokenizer: Tokenizer | None = None

    @property
    def model_name(self) -> str:
        return f"{EMBEDDING_REPO}@{EMBEDDING_REVISION}"

    @property
    def system(self) -> str:
        return "onnxruntime"

    async def embed(
        self,
        inputs: str | Sequence[str],
        *,
        input_type: EmbedInputType,
        settings: EmbeddingSettings | None = None,
    ) -> EmbeddingResult:
        texts, settings = self.prepare_embed(inputs, settings)
        if settings.get("dimensions"):
            raise ValueError("This embedding model always returns its full dimensions.")
        prompted = [QUERY_PROMPT + text for text in texts] if input_type == "query" else texts
        embeddings = await asyncio.to_thread(self._embed, prompted)
        return EmbeddingResult(
            embeddings=embeddings,
            inputs=texts,
            input_type=input_type,
            model_name=self.model_name,
            provider_name=self.system,
        )

    async def max_input_tokens(self) -> int | None:
        return MAX_INPUT_TOKENS

    async def count_tokens(self, text: str) -> int:
        _, tokenizer = self._load()
        return len(tokenizer.encode(text).ids)

    def _load(self) -> tuple[onnxruntime.InferenceSession, Tokenizer]:
        with self._lock:
            if self._session is None or self._tokenizer is None:
                model = self._snapshot / "onnx" / "model.onnx"
                # ONNX Runtime refuses external weights reached through a symbolic link, which
                # is how the Hugging Face cache stores files unless symlinks are disabled.
                if model.is_symlink() or model.with_name("model.onnx_data").is_symlink():
                    raise RuntimeError("the cached embedding model files are symbolic links")
                self._tokenizer = Tokenizer.from_file(str(self._snapshot / "tokenizer.json"))
                self._session = onnxruntime.InferenceSession(
                    str(model), providers=["CPUExecutionProvider"]
                )
            return self._session, self._tokenizer

    def _embed(self, texts: Sequence[str]) -> list[list[float]]:
        session, tokenizer = self._load()
        # The post-processor appends <|endoftext|>, whose hidden state is the embedding.
        encodings = [encoding.ids for encoding in tokenizer.encode_batch(texts)]
        vectors: list[list[float]] = [[] for _ in texts]
        # Similar lengths share a batch to limit padding.
        order = sorted(range(len(texts)), key=lambda index: len(encodings[index]))
        for start in range(0, len(order), BATCH_SIZE):
            batch = order[start : start + BATCH_SIZE]
            lengths = np.array([len(encodings[index]) for index in batch])
            # Right padding keeps every real token at the position it has alone, and causal
            # attention never lets a real token see the padding after it, so a text embeds the
            # same whatever it is batched with.
            input_ids = np.zeros((len(batch), lengths.max()), dtype=np.int64)
            attention_mask = np.zeros_like(input_ids)
            for row, index in enumerate(batch):
                input_ids[row, : lengths[row]] = encodings[index]
                attention_mask[row, : lengths[row]] = 1
            hidden = np.asarray(
                session.run(
                    ["last_hidden_state"],
                    {"input_ids": input_ids, "attention_mask": attention_mask},
                )[0]
            )
            pooled = hidden[np.arange(len(batch)), lengths - 1]
            pooled = pooled / np.linalg.norm(pooled, axis=1, keepdims=True)
            for row, index in enumerate(batch):
                vectors[index] = pooled[row].tolist()
        return vectors
