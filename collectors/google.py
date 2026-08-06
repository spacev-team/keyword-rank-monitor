"""구글 SERP 순위 수집기 — SerpAPI 우선, 직접 스크레이핑 폴백.

구글은 2025년 1월부터 검색 결과에 JS 실행을 요구한다. requests 로 받으면
UA 종류와 무관하게 결과 HTML 대신 벽 페이지가 온다(이 환경에서 실측 확인):
  - 모던/구형 데스크톱·모바일 UA → 'enablejs' 인터스티셜(200, 결과 0)
  - 텍스트 브라우저 UA(Lynx/w3m)   → '더 이상 지원되지 않습니다'(200)
  - 구형 WAP UA                    → 403
  - 실제 Chromium(데이터센터 IP)   → 302 /sorry/ (reCAPTCHA)
따라서 안정 경로는 SERPAPI_KEY 설정(SerpAPI 경유)이며, 직접 스크레이핑은
거주지 IP 등 구글이 아직 HTML 을 주는 환경을 위한 폴백이다. 벽/캡차는 전부
blocked 로 구분 기록해 '권외'와 섞이지 않게 한다.
"""
from __future__ import annotations

import requests
from bs4 import BeautifulSoup

import config
from collectors.base import (
    BaseCollector,
    CollectResult,
    RankRecord,
    make_session,
    polite_sleep,
)

_SEARCH_URL = "https://www.google.com/search"
_SERPAPI_URL = "https://serpapi.com/search"

# 차단/벽 페이지 실측 마커 (2026-08 덤프 기준).
#   /sorry/ 캡차 페이지: URL 에 /sorry/, 본문에 recaptcha·'비정상적인 트래픽'
#   JS 벽: /httpservice/retry/enablejs 로의 meta refresh
_BLOCK_MARKERS = ("recaptcha", "unusual traffic", "비정상적인 트래픽")
_JS_WALL_MARKER = "/httpservice/retry/enablejs"
_UNSUPPORTED_MARKER = "더 이상 지원되지 않습니다"


class GoogleRankCollector(BaseCollector):
    key = "google"
    label = "구글 검색 순위"

    def collect(self, keywords: list[str]) -> CollectResult:
        if config.SERPAPI_KEY:
            return self._collect_serpapi(keywords)
        return self._collect_scrape(keywords)

    # ── SerpAPI 경로 ─────────────────────────────────
    def _collect_serpapi(self, keywords: list[str]) -> CollectResult:
        records: list[RankRecord] = []
        for i, kw in enumerate(keywords):
            if i:
                polite_sleep(1.0)  # API 호출은 차단 위험이 없어 짧게만 쉰다
            try:
                r = requests.get(_SERPAPI_URL, params={
                    "engine": "google", "q": kw, "hl": "ko", "gl": "kr",
                    "num": "20", "api_key": config.SERPAPI_KEY,
                }, timeout=config.REQUEST_TIMEOUT)
                data = r.json()
            except Exception as e:  # noqa: BLE001 — 키워드 단위로 격리
                records += _error_pair(kw, f"serpapi: {e}")
                continue
            if r.status_code != 200 or "error" in data:
                records += _error_pair(kw, f"serpapi {r.status_code}: {data.get('error', '')}")
                continue
            records.append(_rank_serpapi_ads(kw, data.get("ads") or []))
            records.append(_rank_serpapi_organic(kw, data.get("organic_results") or []))
        return CollectResult(records, meta={"path": "serpapi"})

    # ── 직접 스크레이핑 경로 ─────────────────────────
    def _collect_scrape(self, keywords: list[str]) -> CollectResult:
        session = make_session()
        # CONSENT/SOCS: EU 동의 인터스티셜 회피용. SOCS 값은 '동의 완료' 인코딩.
        session.cookies.set("CONSENT", "YES+cb", domain=".google.com")
        session.cookies.set(
            "SOCS", "CAESHAgBEhJnd3NfMjAyMzA4MTAtMF9SQzIaAmtvIAEaBgiA_LyaBg",
            domain=".google.com")

        records: list[RankRecord] = []
        consecutive_blocks = 0
        for i, kw in enumerate(keywords):
            if i:
                polite_sleep(config.GOOGLE_SLEEP_BASE)
            html, block_detail, err = _fetch(session, kw)
            if err:
                records += _error_pair(kw, err)
                continue
            if block_detail:
                consecutive_blocks += 1
                records += _blocked_pair(kw, block_detail)
                if consecutive_blocks >= config.GOOGLE_BLOCK_ABORT:
                    # 차단이 이어지면 재시도할수록 IP 평판만 나빠진다 → 조기 종료
                    for rest in keywords[i + 1:]:
                        records += _blocked_pair(rest, "aborted")
                    break
                continue
            consecutive_blocks = 0
            records += _parse_serp(kw, html)
        return CollectResult(records, meta={"path": "scrape"})


# ── HTTP/차단 감지 ───────────────────────────────────
def _fetch(session: requests.Session, kw: str) -> tuple[str, str, str]:
    """(html, block_detail, error) — block_detail 비면 정상 응답."""
    try:
        r = session.get(_SEARCH_URL, params={
            "q": kw, "hl": "ko", "gl": "kr", "num": "20",
        }, timeout=config.REQUEST_TIMEOUT, allow_redirects=False)
    except Exception as e:  # noqa: BLE001
        return "", "", str(e)
    if r.status_code in (301, 302, 303) and "/sorry" in r.headers.get("Location", ""):
        return "", "sorry_redirect", ""
    if r.status_code == 429:
        return "", "http_429", ""
    if r.status_code != 200:
        return "", "", f"http {r.status_code}"
    body = r.text
    low = body.lower()
    if "/sorry/" in str(r.url) or any(m in low or m in body for m in _BLOCK_MARKERS):
        return "", "captcha", ""
    if _JS_WALL_MARKER in body:
        # 구글 JS 필수화 벽 — 결과 자체를 안 준 것이므로 parse_fail 이 아니라 blocked
        return "", "js_wall", ""
    if _UNSUPPORTED_MARKER in body:
        return "", "unsupported_ua", ""
    return body, "", ""


# ── 스크레이핑 파서 ──────────────────────────────────
def _parse_serp(kw: str, html: str) -> list[RankRecord]:
    soup = BeautifulSoup(html, "lxml")

    # 광고: 상단(#tads)·하단(#bottomads) 컨테이너를 노출 순서대로 통합 순번.
    # 유닛 경계는 [data-text-ad](텍스트 광고 표준 속성), 없으면 컨테이너 직계 div.
    ad_rec = None
    ad_total = 0
    for cid, section in (("tads", "상단광고"), ("bottomads", "하단광고")):
        container = soup.find(id=cid)
        if container is None:
            continue
        units = container.select("[data-text-ad]") or container.find_all(
            "div", recursive=False)
        for unit in units:
            a = unit.find("a", href=True)
            if a is None:
                continue
            ad_total += 1
            # 광고 href 는 googleadservices 리다이렉트여도 목적지 도메인이
            # 쿼리에 포함되므로 is_self_url 의 서브스트링 매칭으로 잡힌다.
            if ad_rec is None and config.is_self_url(a["href"]):
                ad_rec = RankRecord("google", "ad", kw, ad_total, 0,
                                    section=section, matched=a["href"][:200])
    if ad_rec is not None:
        ad_rec.total = ad_total
    elif ad_total:
        ad_rec = RankRecord("google", "ad", kw, None, ad_total, status="not_found")
    else:
        ad_rec = RankRecord("google", "ad", kw, None, 0, status="no_section")

    # 오가닉: h3 를 가진 결과 블록의 첫 <a>. h3 는 <a> 안쪽에 있는 게 기본형이라
    # find_parent 우선, 변형 대비 형제 탐색 폴백. 광고 컨테이너 내부는 제외.
    org_rec = None
    org_total = 0
    root = soup.find(id="search") or soup.find(id="rso") or soup
    for h3 in root.find_all("h3"):
        if h3.find_parent(id="tads") or h3.find_parent(id="bottomads") \
                or h3.find_parent(attrs={"data-text-ad": True}):
            continue
        a = h3.find_parent("a", href=True) or h3.find("a", href=True)
        if a is None:
            continue
        href = a["href"]
        if href.startswith("/url?"):  # 비-JS HTML 은 /url?q=<실제URL> 래핑
            href = href.split("q=", 1)[-1]
        org_total += 1
        if org_rec is None and config.is_self_url(href):
            org_rec = RankRecord("google", "organic", kw, org_total, 0,
                                 section="웹결과", matched=href[:200])
        if org_total >= config.ORGANIC_SCAN_LIMIT:
            break
    if org_rec is not None:
        org_rec.total = org_total
    elif org_total:
        org_rec = RankRecord("google", "organic", kw, None, org_total,
                             status="not_found")
    else:
        # 200 인데 결과 블록 0개 = 구조 변경 의심(벽 페이지는 위에서 걸렀다)
        org_rec = RankRecord("google", "organic", kw, None, 0, status="parse_fail")
    return [ad_rec, org_rec]


# ── SerpAPI 파서 ─────────────────────────────────────
def _rank_serpapi_ads(kw: str, ads: list[dict]) -> RankRecord:
    if not ads:
        return RankRecord("google", "ad", kw, None, 0, status="no_section")
    for i, ad in enumerate(ads, 1):  # SerpAPI 는 상단→하단 노출 순서로 준다
        url = ad.get("link") or ad.get("tracking_link") or ""
        if config.is_self_url(url) or config.is_self_url(ad.get("displayed_link", "")):
            section = "하단광고" if ad.get("block_position") == "bottom" else "상단광고"
            return RankRecord("google", "ad", kw, i, len(ads),
                              section=section, matched=url[:200])
    return RankRecord("google", "ad", kw, None, len(ads), status="not_found")


def _rank_serpapi_organic(kw: str, results: list[dict]) -> RankRecord:
    results = results[:config.ORGANIC_SCAN_LIMIT]
    if not results:
        return RankRecord("google", "organic", kw, None, 0, status="parse_fail")
    for i, item in enumerate(results, 1):
        if config.is_self_url(item.get("link", "")):
            return RankRecord("google", "organic", kw, i, len(results),
                              section="웹결과", matched=item.get("link", "")[:200])
    return RankRecord("google", "organic", kw, None, len(results), status="not_found")


# ── 공용 레코드 헬퍼 ─────────────────────────────────
def _blocked_pair(kw: str, detail: str) -> list[RankRecord]:
    return [RankRecord("google", area, kw, None, 0, status="blocked", detail=detail)
            for area in ("ad", "organic")]


def _error_pair(kw: str, detail: str) -> list[RankRecord]:
    return [RankRecord("google", area, kw, None, 0, status="error", detail=detail)
            for area in ("ad", "organic")]
