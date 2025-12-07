from __future__ import annotations

from typing import Type

from .aachen_family import AachenFamilyCrawler
from .aachener_kinder import AachenerKinderCrawler
from .gruen_metropole import GruenMetropoleCrawler
from .nrw_tourismus import NrwTourismusCrawler
from .kingkalli import KingKalliCrawler
from .rur_eifel import RurEifelCrawler
from .roetgen_event import RoetgenEventCrawler
from .base import BaseCrawler

CRAWLERS: tuple[Type[BaseCrawler], ...] = (
    AachenFamilyCrawler,
    AachenerKinderCrawler,
    GruenMetropoleCrawler,
    NrwTourismusCrawler,
    KingKalliCrawler,
    RurEifelCrawler,
    RoetgenEventCrawler,
)


def run_all_crawlers():
    results = {}
    for crawler_cls in CRAWLERS:
        crawler = crawler_cls()
        results[crawler_cls.__name__] = crawler.run()
    return results
