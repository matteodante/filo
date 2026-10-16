import asyncio
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from pydantic_ai import Embedder

from filo.embedding import QUERY_PROMPT, OnnxQwen3EmbeddingModel

EOS = 0


class FakeTokenizer:
    def encode_batch(self, texts: list[str]) -> list[SimpleNamespace]:
        return [SimpleNamespace(ids=[ord(char) for char in text] + [EOS]) for text in texts]

    def encode(self, text: str) -> SimpleNamespace:
        return self.encode_batch([text])[0]


class FakeSession:
    """A causal stand-in: each position sees only the tokens up to itself."""

    def __init__(self) -> None:
        self.calls: list[np.ndarray] = []

    def run(self, outputs: list[str], feeds: dict[str, np.ndarray]) -> list[np.ndarray]:
        assert outputs == ["last_hidden_state"]
        input_ids = feeds["input_ids"]
        assert input_ids.dtype == np.int64
        assert feeds["attention_mask"].shape == input_ids.shape
        self.calls.append(input_ids)
        features = np.stack(
            [input_ids % 7, input_ids % 11 + 1, np.ones_like(input_ids)], axis=-1
        ).astype(np.float32)
        return [np.cumsum(features, axis=1)]


def _model(tmp_path: Path) -> tuple[OnnxQwen3EmbeddingModel, FakeSession]:
    model = OnnxQwen3EmbeddingModel(tmp_path)
    session = FakeSession()
    model._session = session  # type: ignore[assignment]
    model._tokenizer = FakeTokenizer()  # type: ignore[assignment]
    return model, session


def _expected(text: str) -> list[float]:
    (hidden,) = FakeSession().run(
        ["last_hidden_state"],
        {
            "input_ids": np.array([[ord(char) for char in text] + [EOS]], dtype=np.int64),
            "attention_mask": np.ones((1, len(text) + 1), dtype=np.int64),
        },
    )
    vector = hidden[0, -1]
    return (vector / np.linalg.norm(vector)).tolist()


def test_documents_pool_the_last_real_token_whatever_the_batch(tmp_path: Path) -> None:
    model, session = _model(tmp_path)
    texts = ["a much longer document", "short", "mid length"]

    result = Embedder(model).embed_documents_sync(texts)

    for text, vector in zip(texts, result.embeddings, strict=True):
        assert vector == pytest.approx(_expected(text))
        assert np.linalg.norm(vector) == pytest.approx(1.0)
    # Right padding: every row starts with its own tokens, so positions match an unbatched run.
    (batch,) = session.calls
    assert batch.shape == (3, len("a much longer document") + 1)
    assert list(batch[0, : len("short") + 1]) == [ord(char) for char in "short"] + [EOS]


def test_only_queries_carry_the_instruction(tmp_path: Path) -> None:
    model, _ = _model(tmp_path)
    embedder = Embedder(model)

    query = embedder.embed_query_sync("calls").embeddings[0]
    document = embedder.embed_documents_sync(["calls"]).embeddings[0]

    assert query == pytest.approx(_expected(QUERY_PROMPT + "calls"))
    assert document == pytest.approx(_expected("calls"))


def test_batches_are_bounded_and_keep_input_order(tmp_path: Path) -> None:
    model, session = _model(tmp_path)
    texts = [f"entry {'x' * index}" for index in range(19, -1, -1)]

    result = Embedder(model).embed_documents_sync(texts)

    assert [len(call) for call in session.calls] == [8, 8, 4]
    assert result.embeddings == [pytest.approx(_expected(text)) for text in texts]


def test_reduced_dimensions_are_refused(tmp_path: Path) -> None:
    model, _ = _model(tmp_path)

    with pytest.raises(ValueError, match="full dimensions"):
        asyncio.run(model.embed("calls", input_type="document", settings={"dimensions": 32}))


def test_token_count_includes_end_of_text(tmp_path: Path) -> None:
    model, _ = _model(tmp_path)

    assert asyncio.run(model.count_tokens("abc")) == 4


def test_symlinked_model_files_fail_closed(tmp_path: Path) -> None:
    onnx = tmp_path / "onnx"
    onnx.mkdir()
    (tmp_path / "blob").write_bytes(b"")
    (onnx / "model.onnx").symlink_to(tmp_path / "blob")

    with pytest.raises(RuntimeError, match="symbolic links"):
        Embedder(OnnxQwen3EmbeddingModel(tmp_path)).embed_documents_sync(["calls"])
