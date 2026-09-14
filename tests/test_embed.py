"""Vector text codec (embed.format_vector / parse_vector) for pgvector I/O."""

import numpy as np

from painminer.pipeline.embed import format_vector, parse_vector


def test_format_has_brackets():
    text = format_vector(np.array([0.1, 0.2, 0.3]))
    assert text.startswith("[")
    assert text.endswith("]")


def test_format_six_decimal_places():
    assert format_vector(np.array([0.1, 0.2])) == "[0.100000,0.200000]"


def test_parse_known_string():
    vec = parse_vector("[1.0,2.5,-3.0]")
    assert np.allclose(vec, np.array([1.0, 2.5, -3.0]))


def test_round_trip_small_vector():
    v = np.array([0.1, 0.2, -0.3, 0.0, 1.0])
    assert np.allclose(parse_vector(format_vector(v)), v, atol=1e-5)


def test_round_trip_random_384():
    rng = np.random.default_rng(0)
    v = rng.normal(size=384).astype(np.float64)
    out = parse_vector(format_vector(v))
    assert len(out) == 384
    assert np.allclose(out, v, atol=1e-5)
