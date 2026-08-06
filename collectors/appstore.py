"""App Store 키워드 순위 — iTunes Search API(무료, 결과 순서 = 검색 순위 프록시).

external-metrics-monitor/collectors/app_rank.py 방법론과 동일하되 대상은 자사 1개 앱,
키워드는 이 프로젝트 전체 목록. ⚠️ 실제 스토어 노출은 기기·개인화로 달라질 수 있어
동일 방법론 반복 측정의 추세 추적 용도.
"""
from __future__ import annotations

import requests

import config
from collectors.base import BaseCollector, CollectResult, RankRecord, polite_sleep


class AppStoreRankCollector(BaseCollector):
    key = "appstore"
    label = "App Store 키워드 순위"

    def collect(self, keywords: list[str]) -> CollectResult:
        records: list[RankRecord] = []
        errors = 0
        for kw in keywords:
            try:
                r = requests.get(
                    "https://itunes.apple.com/search",
                    params={"term": kw, "country": "kr", "entity": "software",
                            "limit": config.ITUNES_LIMIT},
                    timeout=config.REQUEST_TIMEOUT)
                r.raise_for_status()
                results = r.json().get("results", [])
                rank = next((n + 1 for n, it in enumerate(results)
                             if str(it.get("trackId")) == config.SELF_IOS_ID), None)
                records.append(RankRecord(
                    self.key, "app", kw, rank, len(results),
                    matched=config.SELF_IOS_ID if rank else "",
                    status="ok" if rank else "not_found"))
            except (requests.RequestException, ValueError) as exc:
                errors += 1
                records.append(RankRecord(self.key, "app", kw, None, 0,
                                          status="error", detail=str(exc)[:200]))
            polite_sleep(config.SLEEP_BASE)  # iTunes ~20req/min 제한 여유
        return CollectResult(records, {"keywords": len(keywords), "errors": errors})
