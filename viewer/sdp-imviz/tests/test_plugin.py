"""T4 for the Imviz plugin with a fake client: it sends the viewer's footprint and the chosen profile,
then tabulates and marks what comes back (INV-012). No kernel, no network."""

from __future__ import annotations

from pathlib import Path

import jdaviz
import pytest
import yaml
from astropy.io import fits

from sdp_imviz import InvestigateError, attach
from sdp_imviz.plugin import MARKERS

from .fakes import PROFILE, FakeClient, image_hdu

pytestmark = pytest.mark.tier("T4")


@pytest.fixture
def profiles(tmp_path: Path) -> str:
    (tmp_path / "comoving.yaml").write_text(yaml.safe_dump(PROFILE))
    return str(tmp_path)


def imviz_with_image() -> jdaviz.Imviz:
    imviz = jdaviz.Imviz()
    imviz.load_data(fits.HDUList([fits.PrimaryHDU(), image_hdu()]), data_label="field")
    return imviz


@pytest.mark.req("INV-012")
def test_the_plugin_sends_the_footprint_and_marks_the_candidates(profiles: str) -> None:
    imviz = imviz_with_image()
    client = FakeClient()
    plg = attach(imviz, profiles_dir=profiles, client=client)
    assert plg.profile.selected == "synthetic-comoving@1.0.0"
    plg.seed, plg.label = 5, "M67 core"
    result = plg.investigate()

    [call] = client.calls
    assert call["region"].startswith("icrs;polygon(") and call["profile"] == PROFILE
    assert call["seed"] == 5 and call["label"] == "M67 core"
    assert plg.results is result and result.label == "EXPLORATORY"
    assert len(plg.table.export_table()) == 3
    assert MARKERS in imviz._app.data_collection.labels


def test_a_typed_region_overrides_the_footprint(profiles: str) -> None:
    client = FakeClient()
    plg = attach(imviz_with_image(), profiles_dir=profiles, client=client)
    plg.region = "icrs;circle(120,-10,0.02)"
    plg.investigate()
    assert client.calls[0]["region"] == "icrs;circle(120,-10,0.02)"


def test_a_refusal_is_shown_and_nothing_is_marked(profiles: str) -> None:
    imviz = imviz_with_image()
    plg = attach(imviz, profiles_dir=profiles, client=FakeClient(fail="sdp-api answered 409: profile hash mismatch"))
    with pytest.raises(InvestigateError):
        plg.investigate()
    assert plg.results is None
    assert MARKERS not in imviz._app.data_collection.labels


def test_clear_removes_results_and_markers(profiles: str) -> None:
    imviz = imviz_with_image()
    plg = attach(imviz, profiles_dir=profiles, client=FakeClient())
    plg.investigate()
    plg.clear()
    assert plg.results is None and not plg.table.export_table()
    assert MARKERS not in imviz._app.data_collection.labels


def test_without_a_profile_nothing_is_sent() -> None:
    client = FakeClient()
    plg = attach(imviz_with_image(), client=client)
    with pytest.raises(InvestigateError, match="profile"):
        plg.investigate()
    assert client.calls == []


def test_the_deconfigged_app_picks_the_plugin_up(profiles: str) -> None:
    app = jdaviz.App()
    plg = attach(app, profiles_dir=profiles, client=FakeClient())
    assert plg.profile.selected == "synthetic-comoving@1.0.0"
