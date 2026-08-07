"""구글 SERP 순위 수집기 — SerpAPI > Serper.dev > 직접 스크레이핑.

구글은 2025년 1월부터 검색 결과에 JS 실행을 요구한다. requests 로 받으면
UA 종류와 무관하게 결과 HTML 대신 벽 페이지가 온다(이 환경에서 실측 확인):
  - 모던/구형 데스크톱·모바일 UA → 'enablejs' 인터스티셜(200, 결과 0)
  - 텍스트 브라우저 UA(Lynx/w3m)   → '더 이상 지원되지 않습니다'(200)
  - 구형 WAP UA                    → 403
  - 실제 Chromium(데이터센터 IP)   → 302 /sorry/ (reCAPTCHA)
거주지 IP + 실제 브라우저는 통하지만(2026-08-07 실측) 상시 켜진 로컬 머신이
필요해 기각(사용자 확정: 100% 클라우드). 구글 공식 Custom Search JSON API 는
2026-01-20부터 신규 고객 차단(403 실측 + 공식 문서, 2027-01-01 서비스 종료)
→ 저가 경로는 Serper.dev(가입 시 2,500쿼리 무료, 이후 선불 크레딧). 크레딧
절약을 위해 구글 수집 키워드를 브랜드 계열로 좁힌다(그룹 필터는 run.py,
config.GOOGLE_KW_GROUPS). 우선순위:
  1) SERPAPI_KEY   → SerpAPI 경유(고가)
  2) SERPER_KEY    → Serper.dev 경유(저가, 결과 수 10 초과 요청은 크레딧 2배)
  3) 직접 스크레이핑 → 데이터센터 IP 에선 사실상 전부 blocked 기록
구글은 organic 만 측정한다 — 광고는 유료 SerpAPI 없이 측정 불가라 프로젝트에서
제외(사용자 확정 2026-08-07). 벽/캡차는 전부 blocked 로 구분 기록해 '미노출'과
섞이지 않게 한다.
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
_SERPER_URL = "https://google.serper.dev/search"

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
        if config.SERPER_KEY:
            return self._collect_serper(keywords)
        return self._collect_scrape(keywords)

    # ── Serper.dev 경로 ──────────────────────────────
    def _collect_serper(self, keywords: list[str]) -> CollectResult:
        """구글은 organic 만 측정(광고 제외 — 모듈 도킹스트링 참고).
        num ≤ 10 이면 1크레딧, 초과는 2크레딧 — config.GOOGLE_API_NUM(기본 10).
        크레딧 소진/키 오류(402·403·429)는 잔여 키워드까지 같은 응답이 확실하므로
        전부 구분 기록하고 즉시 중단."""
        num = config.GOOGLE_API_NUM
        records: list[RankRecord] = []
        for i, kw in enumerate(keywords):
            if i:
                polite_sleep(1.0)  # API 호출은 차단 위험이 없어 짧게만 쉰다
            try:
                r = requests.post(
                    _SERPER_URL,
                    headers={"X-API-KEY": config.SERPER_KEY},
                    json={"q": kw, "gl": "kr", "hl": "ko", "num": num},
                    timeout=config.REQUEST_TIMEOUT)
            except Exception as e:  # noqa: BLE001 — 키워드 단위로 격리
                records.append(_error_organic(kw, f"serper: {e}"))
                continue
            if r.status_code in (402, 403, 429):
                detail = f"serper_quota {r.status_code}"
                records += [_error_organic(k, detail) for k in keywords[i:]]
                break
            if r.status_code != 200:
                records.append(_error_organic(kw, f"serper http {r.status_code}"))
                continue
            records.append(_rank_api_organic(kw, r.json().get("organic") or []))
        return CollectResult(records, meta={"path": "serper"})

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
                records.append(_error_organic(kw, f"serpapi: {e}"))
                continue
            if r.status_code != 200 or "error" in data:
                records.append(_error_organic(kw, f"serpapi {r.status_code}: {data.get('error', '')}"))
                continue
            records.append(_rank_api_organic(kw, data.get("organic_results") or []))
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
                records.append(_error_organic(kw, err))
                continue
            if block_detail:
                consecutive_blocks += 1
                records.append(_blocked_organic(kw, block_detail))
                if consecutive_blocks >= config.GOOGLE_BLOCK_ABORT:
                    # 차단이 이어지면 재시도할수록 IP 평판만 나빠진다 → 조기 종료
                    for rest in keywords[i + 1:]:
                        records.append(_blocked_organic(rest, "aborted"))
                    break
                continue
            consecutive_blocks = 0
            records.append(_parse_serp_organic(kw, html))
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


def _error_organic(kw: str, detail: str) -> RankRecord:
    return RankRecord("google", "organic", kw, None, 0, status="error", detail=detail)


def _rank_api_organic(kw: str, items: list[dict]) -> RankRecord:
    """API(Serper/SerpAPI) organic 목록은 노출 순서 그대로 — 자사 첫 매칭 순번이 순위."""
    items = items[:config.ORGANIC_SCAN_LIMIT]
    if not items:
        return RankRecord("google", "organic", kw, None, 0, status="not_found")
    for i, item in enumerate(items, 1):
        if config.is_self_url(item.get("link", "")):
            return RankRecord("google", "organic", kw, i, len(items),
                              section="웹결과", matched=item.get("link", "")[:200])
    return RankRecord("google", "organic", kw, None, len(items), status="not_found")


# ── 스크레이핑 파서 ──────────────────────────────────
def _parse_serp_organic(kw: str, html: str) -> RankRecord:
    soup = BeautifulSoup(html, "lxml")
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
        return org_rec
    if org_total:
        return RankRecord("google", "organic", kw, None, org_total,
                          status="not_found")
    # 200 인데 결과 블록 0개 = 구조 변경 의심(벽 페이지는 위에서 걸렀다)
    return RankRecord("google", "organic", kw, None, 0, status="parse_fail")


# ── 공용 레코드 헬퍼 ─────────────────────────────────
def _blocked_organic(kw: str, detail: str) -> RankRecord:
    return RankRecord("google", "organic", kw, None, 0, status="blocked", detail=detail)
