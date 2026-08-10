"""네이버 검색량(키워드도구) 수집 — 전체 모니터링 키워드의 최근 30일 검색수.

EMM(external-metrics-monitor) 검색량_네이버 탭과 같은 API·같은 계정을 쓰되, 대상이
다르다: EMM 은 브랜드·경쟁사 13개(일자별 시트 적재), 여기는 순위 모니터링 240개
키워드 전부(대시보드 조인용). 시트를 조인하지 않고 직접 호출하는 이유 — 시트에는
우리 키워드가 5개만 있다(2026-08-10 확인).

- /keywordstool 의 monthly*QcCnt = 최근 30일 검색수. 대시보드는 ÷30 일평균으로 표시.
- hintKeywords 는 공백 불가 → 공백 제거 후 조회, 응답 relKeyword 를 정규화 매칭.
  ('삼삼엠투 후기'와 '삼삼엠투후기'는 같은 키워드로 집계됨 — 정규화 중복은 1회만 호출)
- '< 10' 응답은 10 미만 표기 — 0 으로 강등하지 않고 5 로 기록(구간 중앙값, 0 은
  '수요 없음'으로 오독됨).
- 하루 1회(daily 런)면 충분 — 30일 윈도우 값이라 일중 변화가 없다.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time

import requests

import config
from collectors.base import polite_sleep, today_kst

_BASE = "https://api.searchad.naver.com"
_BATCH = 5  # hintKeywords 최대 5개/호출

_SCHEMA = """
CREATE TABLE IF NOT EXISTS volumes (
    date    TEXT NOT NULL,   -- YYYY-MM-DD (수집일)
    keyword TEXT NOT NULL,   -- 모니터링 키워드 원문
    pc      INTEGER,         -- 최근 30일 PC 검색수 (NULL = API 미매칭)
    mo      INTEGER,
    total   INTEGER,
    PRIMARY KEY (date, keyword)
)
"""


def _sign(ts: str, method: str, uri: str, secret: str) -> str:
    msg = f"{ts}.{method}.{uri}"
    return base64.b64encode(
        hmac.new(secret.encode(), msg.encode(), hashlib.sha256).digest()).decode()


def _to_int(v) -> int:
    s = str(v).replace(",", "").strip()
    if s.startswith("<"):
        return 5  # '< 10' — 구간 중앙값(0 은 수요 없음으로 오독)
    try:
        return int(s)
    except ValueError:
        return 0


class _Client:
    def __init__(self, api_key: str, secret: str, customer_id: str):
        self.api_key, self.secret, self.customer_id = api_key, secret, str(customer_id)

    def keywordstool(self, hints: list[str]) -> list[dict]:
        uri = "/keywordstool"
        ts = str(int(time.time() * 1000))
        r = requests.get(_BASE + uri,
                         params={"hintKeywords": ",".join(hints), "showDetail": "1"},
                         headers={"X-Timestamp": ts, "X-API-KEY": self.api_key,
                                  "X-Customer": self.customer_id,
                                  "X-Signature": _sign(ts, "GET", uri, self.secret)},
                         timeout=30)
        r.raise_for_status()
        return r.json().get("keywordList", [])


def collect_and_store(db, keywords: list[str]) -> dict:
    """키워드 검색량 수집 → volumes 테이블 upsert. 반환: meta."""
    if not config.NAVER_SEARCHAD_ACCOUNTS:
        return {"skipped": "NAVER_SEARCHAD_ACCOUNTS 미설정"}
    acct = config.NAVER_SEARCHAD_ACCOUNTS[0]
    client = _Client(acct["api_key"], acct["secret"], acct["customer_id"])
    db.executescript(_SCHEMA)

    # 정규화(공백 제거·대문자) 기준으로 API 호출을 dedupe — 원문 키워드들이 공유
    norm_to_kws: dict[str, list[str]] = {}
    for kw in keywords:
        norm_to_kws.setdefault(kw.replace(" ", "").upper(), []).append(kw)

    date = today_kst()
    norms = list(norm_to_kws)
    found, errors = 0, 0
    rows: list[tuple] = []
    for i in range(0, len(norms), _BATCH):
        batch = norms[i:i + _BATCH]
        try:
            kl = client.keywordstool([n for n in batch])
        except Exception:
            errors += 1
            kl = []
        by_norm = {}
        for item in kl:
            n = str(item.get("relKeyword", "")).replace(" ", "").upper()
            by_norm.setdefault(n, item)  # 연관키워드도 오지만 정확 매칭만 사용
        for n in batch:
            item = by_norm.get(n)
            pc = _to_int(item.get("monthlyPcQcCnt")) if item else None
            mo = _to_int(item.get("monthlyMobileQcCnt")) if item else None
            total = (pc + mo) if item else None
            if item:
                found += 1
            for kw in norm_to_kws[n]:
                rows.append((date, kw, pc, mo, total))
        polite_sleep(0.7)  # 검색광고 API 속도 제한 여유
    with db:
        db.executemany(
            "INSERT OR REPLACE INTO volumes VALUES (?,?,?,?,?)", rows)
    return {"date": date, "api_calls": (len(norms) + _BATCH - 1) // _BATCH,
            "matched": found, "unmatched": len(norms) - found, "errors": errors}


def latest_map(db) -> dict[str, int]:
    """최신 수집일 기준 {키워드: 30일 총검색수} — 테이블 없거나 비면 빈 dict."""
    try:
        row = db.execute("SELECT MAX(date) FROM volumes").fetchone()
    except Exception:
        return {}
    if not row or not row[0]:
        return {}
    return {kw: total for kw, total in db.execute(
        "SELECT keyword, total FROM volumes WHERE date = ? AND total IS NOT NULL",
        (row[0],))}
