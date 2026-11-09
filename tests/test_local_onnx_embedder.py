"""The local ONNX embedder. Skips when fastembed is not installed."""

import math

import pytest

pytest.importorskip("fastembed")

from aip.providers.local_onnx import DIMENSION, LocalOnnxEmbedder  # noqa: E402


def _cos(a, b):
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def test_dimension_and_similarity():
    emb = LocalOnnxEmbedder()
    vectors = emb.embed(
        [
            "How long do I have to return a tent?",
            "What is the time limit for sending back a tent?",
            "The quarterly invoice is overdue by three weeks.",
        ]
    )
    assert all(len(v) == DIMENSION == 384 for v in vectors)
    assert _cos(vectors[0], vectors[1]) > _cos(vectors[0], vectors[2]) + 0.1
