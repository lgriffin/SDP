"""The Imviz tray plugin: a driving adapter over InvestigateClient (Investigate §7, INV-012).

It reads the image viewer's footprint as the field, posts it with the selected profile, and overlays
the candidates with the same marker machinery Imviz's Catalogs plugin uses. It holds no analysis
logic and makes no archive call; the kernel does the work behind sdp-api.
"""

from __future__ import annotations

from typing import Any, ClassVar

from astropy.coordinates import SkyCoord
from astropy.table import Table
from jdaviz.core.events import SnackbarMessage
from jdaviz.core.registries import tray_registry
from jdaviz.core.template_mixin import (
    PluginTemplateMixin,
    SelectPluginComponent,
    TableMixin,
    ViewerSelectMixin,
    with_spinner,
)
from jdaviz.core.user_api import PluginUserApi
from traitlets import Bool, Int, List, Unicode

from sdp_imviz.client import HttpInvestigateClient, InvestigateClient, InvestigateError, RunResult
from sdp_imviz.field import footprint_region
from sdp_imviz.profiles import load_profiles

NAME = "sdp-investigate"
MARKERS = "sdp_investigate_candidates"

__all__ = ["Investigate", "attach"]


@tray_registry(NAME, label="Investigate", category="data:analysis")
class Investigate(PluginTemplateMixin, ViewerSelectMixin, TableMixin):
    """Run InvestigateField over the viewer's footprint and mark the candidates.

    Public API: ``profile`` (selection), ``region`` (empty means the viewer footprint), ``seed``,
    ``label``, :meth:`investigate`, :meth:`clear` and ``results`` (the last :class:`RunResult`).
    """

    template_file = __file__, "investigate.vue"
    profile_items = List([]).tag(sync=True)
    profile_selected = Unicode("").tag(sync=True)
    region = Unicode("").tag(sync=True)
    seed = Int(0).tag(sync=True)
    label = Unicode("").tag(sync=True)
    set_id = Unicode("").tag(sync=True)
    number_of_candidates = Int(0).tag(sync=True)
    results_available = Bool(False).tag(sync=True)
    error = Unicode("").tag(sync=True)

    headers: ClassVar[list[str]] = ["candidate", "score", "state", "ra_deg", "dec_deg", "investigation"]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._client: InvestigateClient = HttpInvestigateClient()
        self._profiles: dict[str, dict[str, Any]] = {}
        self._results: RunResult | None = None
        self.docs_description = (
            "Investigate the field in view: identify, retrieve, test and rank under a versioned profile."
            " Every candidate is EXPLORATORY."
        )
        self.profile = SelectPluginComponent(self, items="profile_items", selected="profile_selected")
        self.viewer.add_filter("is_imviz_image_viewer")
        self.table.headers_avail = self.headers
        self.table.headers_visible = self.headers
        self.table.item_key = "investigation"
        self.table._clear_callback = self._clear_markers

    @property
    def user_api(self) -> PluginUserApi:
        return PluginUserApi(
            self,
            expose=("profile", "region", "seed", "label", "investigate", "clear", "results", "table", "use_profiles"),
        )

    # -- configuration ----------------------------------------------------------------------------

    def configure(self, *, client: InvestigateClient | None = None, api: str | None = None) -> None:
        if client is not None:
            self._client = client
        elif api is not None:
            self._client = HttpInvestigateClient(api)

    def use_profiles(self, profiles: dict[str, dict[str, Any]] | str) -> None:
        """Offer these profiles (``name@version`` → document), or every profile in a directory."""
        self._profiles = load_profiles(profiles) if isinstance(profiles, str) else dict(profiles)
        self.profile.choices = sorted(self._profiles)
        if self._profiles and self.profile_selected not in self._profiles:
            self.profile_selected = sorted(self._profiles)[0]

    # -- the run ------------------------------------------------------------------------------------

    @property
    def results(self) -> RunResult | None:
        return self._results

    def field_region(self) -> str:
        """The region typed in, or the selected image viewer's footprint."""
        if self.region.strip():
            return self.region.strip()
        viewer = self.viewer.selected_obj
        data = viewer.state.reference_data
        if data is None or data.coords is None:
            raise InvestigateError("the viewer has no image with a WCS; load one or type a region")
        ny, nx = data.shape
        s = viewer.state
        return footprint_region(data.coords, s.x_min, s.x_max, s.y_min, s.y_max, nx, ny)

    @with_spinner()
    def investigate(self) -> RunResult:
        """Call InvestigateField on the field, then mark and tabulate the candidates."""
        self.error = ""
        if self.profile_selected not in self._profiles:
            raise InvestigateError("choose a profile first (use_profiles)")
        try:
            result = self._client.investigate(
                self.field_region(), self._profiles[self.profile_selected], seed=int(self.seed), label=self.label
            )
        except InvestigateError as exc:
            self.error = str(exc)
            self.hub.broadcast(SnackbarMessage(f"Investigate failed: {exc}", color="error", sender=self))
            raise
        self._show(result)
        return result

    def vue_investigate(self, *args: Any) -> None:
        try:
            self.investigate()
        except InvestigateError:
            pass

    def clear(self) -> None:
        self.table.clear_table()
        self._results = None
        self.results_available, self.number_of_candidates, self.set_id = False, 0, ""

    def _clear_markers(self) -> None:
        viewer = self.viewer.selected_obj
        if viewer is not None and MARKERS in self.app.data_collection.labels:
            viewer.remove_markers(marker_name=MARKERS)

    def _show(self, result: RunResult) -> None:
        self.table.clear_table()
        rows = result.rows()
        for row in rows:
            self.table.add_item(row)
        self._results = result
        self.set_id = result.set_id
        self.number_of_candidates = len(result.candidates)
        self.results_available = True
        if rows:
            markers = Table(
                {
                    "coord": SkyCoord([r["ra_deg"] for r in rows], [r["dec_deg"] for r in rows], unit="deg"),
                    "candidate": [r["candidate"] for r in rows],
                }
            )
            self.viewer.selected_obj.add_markers(markers, use_skycoord=True, marker_name=MARKERS)


def attach(viz: Any, api: str | None = None, profiles_dir: str | None = None, client: Any = None) -> Any:
    """Add the plugin to a Jdaviz app and return its user API. A deconfigged ``jdaviz.App`` picks
    registered plugins up itself; a configured app (``Imviz``) gets the tray item appended."""
    app = viz._app
    try:
        plugin = app.get_tray_item_from_name(NAME)
    except KeyError:
        if app.config == "deconfigged":
            app.update_tray_items_from_registry()
        else:
            app.state.tray_items.append(app._create_tray_item(tray_registry.members[NAME]))
        plugin = app.get_tray_item_from_name(NAME)
    plugin.configure(client=client, api=api)
    if profiles_dir is not None:
        plugin.use_profiles(profiles_dir)
    return plugin.user_api
