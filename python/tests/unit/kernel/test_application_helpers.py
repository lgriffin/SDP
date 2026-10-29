from __future__ import annotations

import astropy.units as u
import numpy as np
import pyarrow as pa
import pytest

from sdp.kernel.application import codec
from sdp.kernel.application.rocrate import build_crate_metadata, read_crate, verify_crate, zip_crate
from sdp.kernel.domain.errors import InvariantViolation
from sdp.kernel.domain.provenance import ProvGraph

pytestmark = pytest.mark.tier("T1")


def test_codec_round_trips_supported_values() -> None:
    value = {
        "a": 1,
        "b": [1.5, None, True],
        "c": (1, "x"),
        "arr": np.arange(6, dtype=np.float32).reshape(2, 3),
        "q": np.array([1.0, 2.0]) * u.arcsec,
        "nan": float("nan"),
        "tbl": pa.table({"x": [1, 2]}),
    }
    back = codec.loads(codec.dumps(value))
    assert back["a"] == 1 and back["b"] == [1.5, None, True] and back["c"] == (1, "x")
    assert np.array_equal(back["arr"], value["arr"]) and back["arr"].dtype == np.float32
    assert back["q"].unit == u.arcsec and np.allclose(back["q"].value, [1.0, 2.0])
    assert np.isnan(back["nan"])
    assert back["tbl"].equals(value["tbl"])


def test_codec_is_canonical() -> None:
    assert codec.dumps({"b": 1, "a": 2}) == codec.dumps({"a": 2, "b": 1})


def test_codec_refuses_objects() -> None:
    with pytest.raises(InvariantViolation):
        codec.dumps(object())
    with pytest.raises(InvariantViolation):
        codec.dumps(np.array([object()]))


def test_rocrate_bundles_are_deterministic_and_verifiable() -> None:
    files = {"manifest.json": (b"{}", "application/json", "m"), "figures/a.svg": (b"<svg/>", "image/svg+xml", "a")}
    meta = build_crate_metadata(
        name="n",
        description="d",
        licence="CC-BY-4.0",
        files=files,
        authors=["r"],
        date_published="2026-10-28",
        citations=[],
    )
    a, b = zip_crate(meta, files), zip_crate(meta, files)
    assert a == b
    assert verify_crate(a) == []
    metadata, contents = read_crate(a)
    assert metadata["@graph"][1]["hasPart"] == [{"@id": "figures/a.svg"}, {"@id": "manifest.json"}]
    assert contents["manifest.json"] == b"{}"


def test_tampered_crate_is_detected() -> None:
    files = {"manifest.json": (b"{}", "application/json", "m")}
    meta = build_crate_metadata(
        name="n", description="d", licence="x", files=files, authors=[], date_published="2026-10-28", citations=[]
    )
    tampered = zip_crate(meta, {"manifest.json": (b'{"x":1}', "application/json", "m")})
    assert any("checksum" in p for p in verify_crate(tampered))


def test_prov_subgraph_and_round_trip() -> None:
    g = ProvGraph()
    g.activity("experiment/e1")
    g.entity("input")
    g.entity("raw")
    g.activity("acq")
    g.relate("used", "experiment/e1", "input")
    g.relate("wasDerivedFrom", "input", "raw")
    g.relate("wasGeneratedBy", "raw", "acq")
    g.entity("unrelated")
    sub = g.subgraph("experiment/e1")
    assert "sdp:raw" in sub.entities and "sdp:acq" in sub.activities and "sdp:unrelated" not in sub.entities
    assert ProvGraph.from_prov_json(g.to_prov_json()).to_prov_json() == g.to_prov_json()


def test_codec_checkpoints_numpy_floats_as_floats() -> None:
    for value in (np.float64(2.5), np.float64(2.0), np.float64("inf")):
        back = codec.loads(codec.dumps({"v": value}))["v"]
        assert type(back) is float and back == float(value)
