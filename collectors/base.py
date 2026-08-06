"""수집기 공통 계약 + HTTP 유틸.

RankRecord 하나 = (엔진 × 영역 × 키워드) 1회 측정. 자사가 안 보이면 rank=None,
status 로 미노출/차단/파싱실패를 구분한다(권외와 수집실패를 섞지 않기 위함 —
external-metrics-monitor app_rank 의 '히어로 카드 권외 오기록' 교훈).
"""
from __future__ import annotations

import datetime as _dt
import random
import time
from dataclasses import dataclass, field

import requests

KST = _dt.timezone(_dt.timedelta(hours=9))


def now_kst() -> str:
    return _dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


def today_kst() -> str:
    return _dt.datetime.now(KST).strftime("%Y-%m-%d")


# status 값: ok(자사 발견) · not_found(정상 파싱, 자사 미노출) · no_section(해당 영역
# 자체가 SERP 에 없음 — 예: 광고 0개) · blocked(봇 차단/캡차) · parse_fail(구조 변경
# 의심 — 페이지는 왔는데 결과 0개 추출) · error(네트워크 등 예외)
@dataclass
class RankRecord:
    engine: str            # naver|google|daum|playstore|appstore
    area: str              # ad|organic|app
    keyword: str
    rank: int | None       # 영역 내 1-base 순위, 미노출/실패 시 None
    total: int             # 해당 영역에서 스캔한 결과 수
    section: str = ""      # 매칭 위치 상세(파워링크, 웹사이트, 블로그 등)
    matched: str = ""      # 매칭된 URL 또는 타이틀
    status: str = "ok"
    detail: str = ""       # 오류 메시지 등 부가정보


@dataclass
class CollectResult:
    records: list[RankRecord]
    meta: dict = field(default_factory=dict)


class BaseCollector:
    key: str = ""
    label: str = ""

    def collect(self, keywords: list[str]) -> CollectResult:  # pragma: no cover
        raise NotImplementedError


# ── HTTP ─────────────────────────────────────────────
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def make_session(extra_headers: dict | None = None) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.5",
    })
    if extra_headers:
        s.headers.update(extra_headers)
    return s


def polite_sleep(base: float) -> None:
    """키워드 간 대기 — 고정 주기 봇 시그니처를 피하려 ±40% 지터."""
    time.sleep(base * random.uniform(0.6, 1.4))
