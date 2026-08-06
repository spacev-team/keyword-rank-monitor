"""Play Store 키워드 순위 — google_play_scraper.search(첫 페이지 최대 30).

히어로 카드(브랜드 검색 최상단) appId 누락 이슈는 external-metrics-monitor 에서
검증된 보조 스펙 주입으로 대응(2026-07-23 '브랜드 키워드 6일 연속 권외 오기록' 교훈).
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from google_play_scraper import search as play_search
from google_play_scraper.constants.element import ElementSpec, ElementSpecs

import config
from collectors.base import BaseCollector, CollectResult, RankRecord, polite_sleep

# 히어로 카드 페이로드의 [2,41,0,2] = 앱 상세 URL(…/details?id=<pkg>) — appId 복원용.
ElementSpecs.SearchResultOnTop.setdefault(
    "appIdFromUrl",
    ElementSpec(None, [2, 41, 0, 2],
                lambda u: parse_qs(urlparse(u).query)["id"][0]))


class PlayStoreRankCollector(BaseCollector):
    key = "playstore"
    label = "Play Store 키워드 순위"

    def collect(self, keywords: list[str]) -> CollectResult:
        records: list[RankRecord] = []
        errors = 0
        for kw in keywords:
            try:
                res = play_search(kw, lang="ko", country="kr", n_hits=config.PLAY_HITS)
                rank = None
                for n, it in enumerate(res):
                    aid = it.get("appId") or it.get("appIdFromUrl")
                    if aid == config.SELF_PLAY_PKG:
                        rank = n + 1
                        break
                records.append(RankRecord(
                    self.key, "app", kw, rank, len(res),
                    matched=config.SELF_PLAY_PKG if rank else "",
                    status="ok" if rank else "not_found"))
            except Exception as exc:  # 라이브러리가 비정형 예외를 던짐 — 키워드 단위 격리
                errors += 1
                records.append(RankRecord(self.key, "app", kw, None, 0,
                                          status="error", detail=str(exc)[:200]))
            polite_sleep(config.SLEEP_BASE)
        return CollectResult(records, {"keywords": len(keywords), "errors": errors})
