from __future__ import annotations

from cosplaytele_mcp.htmlutil import host_key
from cosplaytele_mcp.http import Http
from cosplaytele_mcp.models import SourceId
from cosplaytele_mcp.sources.base import GallerySource, SourceError
from cosplaytele_mcp.sources.beauty3600000 import Beauty3600000Source
from cosplaytele_mcp.sources.cosplaytele import CosplayTeleSource
from cosplaytele_mcp.sources.cup2d import Cup2DSource
from cosplaytele_mcp.sources.everia import EveriaSource
from cosplaytele_mcp.sources.foamgirl import FoamGirlSource
from cosplaytele_mcp.sources.fourkhd import FourKHDSource
from cosplaytele_mcp.sources.hentaicosplay import HentaiCosplaySource
from cosplaytele_mcp.sources.kiutaku import KiutakuSource
from cosplaytele_mcp.sources.misskon import MissKonSource
from cosplaytele_mcp.sources.mitaku import MitakuSource
from cosplaytele_mcp.sources.ososedki import OsosedkiSource

SOURCE_TYPES = (
    CosplayTeleSource,
    HentaiCosplaySource,
    EveriaSource,
    MissKonSource,
    FourKHDSource,
    KiutakuSource,
    Cup2DSource,
    Beauty3600000Source,
    FoamGirlSource,
    OsosedkiSource,
    MitakuSource,
)

HOST_TO_SOURCE = {host_key(cls.base_url): cls.id for cls in SOURCE_TYPES}


class SourceRegistry:
    def __init__(self, http: Http) -> None:
        self._sources = {cls.id: cls(http) for cls in SOURCE_TYPES}

    def get(self, source_id: SourceId) -> GallerySource:
        try:
            return self._sources[source_id]
        except KeyError as exc:
            raise SourceError(f"Unknown source {source_id!r}") from exc

    def all(self) -> list[GallerySource]:
        return list(self._sources.values())

    def by_url(self, url: str) -> GallerySource:
        host = host_key(url)
        if not host:
            raise SourceError(f"Not a gallery URL: {url}")
        source_id = HOST_TO_SOURCE.get(host)
        if source_id is None:
            known = ", ".join(sorted({host_key(cls.base_url) for cls in SOURCE_TYPES}))
            raise SourceError(f"No source for host {host!r}. Known hosts: {known}")
        return self.get(source_id)


__all__ = ["GallerySource", "SourceError", "SourceRegistry"]
