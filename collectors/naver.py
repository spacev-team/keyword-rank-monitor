"""네이버 통합검색(PC) 키워드 순위 수집기 — 광고/오가닉 영역 분리 측정.

엔드포인트 채택 근거: PC(search.naver.com)와 모바일(m.search.naver.com) 응답 HTML 을
모두 덤프해 비교한 결과 PC 쪽 광고 마킹이 명시적이다 — 파워링크는
`div.sc_new.ad_section`(내부 `.nad_area li.lst` 가 광고 1건), 브랜드검색은
`div.brand_search` 클래스로 확정 구분되는 반면, 모바일은 `#main_pack` 도 없고
광고 래퍼가 `section.sc.sp_brand` 처럼 오가닉 섹션과 같은 계열 클래스라 구분이
약하다. 따라서 PC 통합검색을 채택.

파싱 메모(2026-08 구조):
- 결과 블록 = `#main_pack` 하위 `div.sc_new` / `div.brand_search` / `#place-app-root`
  중 최상위(서로 중첩 시 바깥쪽만). 문서 순서가 곧 노출 순서다.
- 광고 블록 판별: `ad_section`·`brand_search` 클래스, 또는 블록 안에
  ader.naver.com 클릭 링크 존재(파워컨텐츠류 네이티브 광고가 이 케이스).
- 파워링크 광고의 랜딩 URL 은 ader 리다이렉트라 href 만으로는 자사 판별이 안 되고,
  표시 URL 텍스트(`a.lnk_url`)와 onclick 의 `urlencode("<원본URL>")` 인자에 원본이
  남는다 → 둘 다 후보로 모아 config.is_self_url 에 넣는다.
- 브랜드검색/파워컨텐츠 블록은 내부 아이템 마크업이 해시 클래스라 분해가 취약하다
  → 블록 전체를 광고 유닛 1개로 취급하고 블록 원문에서 http(s) URL 을 긁어 판별.
- 오가닉 섹션명은 헤더가 없는 블록이 많아(개별 웹문서 카드) 링크 호스트로 보완.
- AI 브리핑 등 '결과가 아닌 SERP 피처' 블록은 오가닉 순번에서 제외한다(2026-08-06
  사용자 리포트: 33M2 오가닉이 2위로 기록됐는데 실제 첫 결과는 자사 웹사이트 —
  1번 자리를 AI 브리핑 답변 박스가 차지). 랭크트래커 관례상 답변박스·연관검색어류는
  별도 피처이지 경쟁 '결과'가 아니며, 포함하면 피처 노출 여부에 따라 순위가 흔들려
  추세 비교가 깨진다.
"""
from __future__ import annotations

import re
import time

from bs4 import BeautifulSoup

import config
from collectors.base import (BaseCollector, CollectResult, RankRecord,
                             make_session, polite_sleep)

_ENDPOINT = "https://search.naver.com/search.naver"

# 차단/캡차 페이지 판별 문구 — 네이버는 봇 판정 시 403 또는 자동입력 방지 안내를 준다
_BLOCK_MARKERS = ("자동입력 방지", "비정상적인 검색", "captcha", "wtm_spam")

# 헤더 없는 오가닉 블록의 섹션명 보완용 호스트 → 이름 매핑
_HOST_SECTIONS = (
    ("blog.naver.com", "블로그"),
    ("cafe.naver.com", "카페"),
    ("kin.naver.com", "지식iN"),
    ("news.naver.com", "뉴스"),
    ("shopping.naver.com", "쇼핑"),
    ("terms.naver.com", "지식백과"),
)

_RAW_URL_RE = re.compile(r"https?://[^\"'\s\\]+")
_ONCLICK_URL_RE = re.compile(r'urlencode\("(https?://[^"]+)"\)')

# 오가닉 순번에서 제외하는 SERP 피처 블록 — 헤더 텍스트 접두 매칭
_FEATURE_SECTIONS = ("AI 브리핑", "연관 검색어", "인기주제", "함께 많이 찾는")


def _outer_blocks(main_pack) -> list:
    """문서 순서의 최상위 결과 블록만 — 파워링크처럼 sc_new 가 래퍼 안에 중첩되는
    경우가 있어 자식 순회 대신 select 후 조상 중복을 걸러낸다."""
    cand = main_pack.select("div.sc_new, div.brand_search, #place-app-root")
    picked = set()
    out = []
    for el in cand:
        if any(id(p) in picked for p in el.parents):
            continue
        picked.add(id(el))
        out.append(el)
    return out


def _header_of(blk) -> str:
    h = blk.select_one("h2, .fds-comps-header-headline, .api_title, .title_area strong")
    return h.get_text(" ", strip=True) if h else ""


def _is_ad_block(blk) -> bool:
    cls = blk.get("class") or []
    if "ad_section" in cls or "brand_search" in cls:
        return True
    return blk.select_one('a[href*="ader.naver.com"]') is not None


def _ad_units(blk) -> list[tuple[str, list[str]]]:
    """광고 블록 → (섹션명, 자사판별 후보문자열들) 유닛 목록, 노출 순서."""
    cls = blk.get("class") or []
    if "ad_section" in cls:
        units = []
        for li in blk.select(".nad_area li.lst") or blk.select("li"):
            cands = []
            # 콤마 셀렉터는 문서순 첫 매칭이라 조상(.url_area)이 먼저 잡힘 → 명시 우선순위
            disp = li.select_one("a.lnk_url") or li.select_one(".url_area")
            if disp is not None:
                cands.append(disp.get_text(strip=True))
            cands.extend(_ONCLICK_URL_RE.findall(str(li)))
            cands.extend(a.get("href", "") for a in li.select("a[href]"))
            units.append(("파워링크", cands))
        return units
    if "brand_search" in cls:
        section = "브랜드검색"
    else:
        section = _header_of(blk)[:20] or "콘텐츠광고"
    # 원본 URL 이 onclick·데이터 속성 등 어디에 있을지 모르므로 블록 원문에서 수집
    return [(section, _RAW_URL_RE.findall(str(blk)))]


def _organic_section(blk) -> str:
    h = _header_of(blk)
    if h:
        return h[:20]
    if blk.select_one(".fds-web-root") is not None:
        return "웹사이트"
    if (blk.get("id") or "") == "place-app-root":
        return "플레이스"
    for a in blk.select("a[href]"):
        for host, name in _HOST_SECTIONS:
            if host in a["href"]:
                return name
    return "기타"


class NaverRankCollector(BaseCollector):
    key = "naver"
    label = "네이버 통합검색 순위"

    # 차단 대응(2026-08-06 실사고: Actions 러너에서 2번째 키워드부터 240개 연속 차단
    # — 아침 런은 정상, 러너 IP 평판 복불복): 첫 차단 시 1회 쿨다운 후 재시도(일시
    # 오탐 회복), 그래도 연속 N개 차단이면 잔여 키워드를 aborted 로 채우고 조기 종료
    # — 차단된 IP 로 계속 때리면 평판만 악화되고 데이터는 안 나온다(구글과 동일 정책).
    BLOCK_ABORT = 5
    BLOCK_COOLDOWN_SEC = 60

    def collect(self, keywords: list[str]) -> CollectResult:
        session = make_session({"Referer": "https://www.naver.com/"})
        records: list[RankRecord] = []
        streak = 0          # 연속 차단 키워드 수
        cooled = False      # 쿨다운 재시도는 런당 1회
        for i, kw in enumerate(keywords):
            if i:
                polite_sleep(config.SLEEP_BASE)
            recs = self._collect_one(session, kw)
            if recs[0].status == "blocked" and not cooled:
                cooled = True
                time.sleep(self.BLOCK_COOLDOWN_SEC)
                session = make_session({"Referer": "https://www.naver.com/"})  # 세션 지문 갱신
                recs = self._collect_one(session, kw)
            records.extend(recs)
            streak = streak + 1 if recs[0].status == "blocked" else 0
            if streak >= self.BLOCK_ABORT:
                for rest in keywords[i + 1:]:
                    records.extend(
                        RankRecord("naver", area, rest, None, 0,
                                   status="blocked", detail="aborted")
                        for area in ("ad", "organic"))
                break
        return CollectResult(records, meta={"endpoint": _ENDPOINT, "device": "pc",
                                            "block_aborted": streak >= self.BLOCK_ABORT})

    # ── 키워드 1개 → ad/organic 레코드 2개 ──────────────
    def _collect_one(self, session, kw: str) -> list[RankRecord]:
        def both(status: str, detail: str = "") -> list[RankRecord]:
            return [RankRecord("naver", area, kw, None, 0, status=status, detail=detail)
                    for area in ("ad", "organic")]

        try:
            resp = session.get(_ENDPOINT, params={"query": kw},
                               timeout=config.REQUEST_TIMEOUT)
        except Exception as e:  # 네트워크 계열 — 차단과 구분해 기록
            return both("error", f"{type(e).__name__}: {e}")

        if resp.status_code in (403, 429):
            return both("blocked", f"HTTP {resp.status_code}")
        if resp.status_code != 200:
            return both("error", f"HTTP {resp.status_code}")
        head = resp.text[:3000]
        if any(m in head for m in _BLOCK_MARKERS):
            return both("blocked", "차단/캡차 안내 페이지")

        main_pack = BeautifulSoup(resp.text, "lxml").select_one("#main_pack")
        if main_pack is None:
            return both("parse_fail", "#main_pack 없음 — SERP 구조 변경 의심")

        ad_units: list[tuple[str, list[str]]] = []
        organic: list[tuple[str, object]] = []
        for blk in _outer_blocks(main_pack):
            if _is_ad_block(blk):
                ad_units.extend(_ad_units(blk))
            else:
                section = _organic_section(blk)
                if section.startswith(_FEATURE_SECTIONS):
                    continue  # 답변박스·연관검색어류 — 결과 아님
                organic.append((section, blk))

        return [self._rank_ad(kw, ad_units), self._rank_organic(kw, organic)]

    @staticmethod
    def _rank_ad(kw: str, units: list[tuple[str, list[str]]]) -> RankRecord:
        if not units:
            return RankRecord("naver", "ad", kw, None, 0, status="no_section")
        for pos, (section, cands) in enumerate(units, start=1):
            hit = next((c for c in cands if config.is_self_url(c)), None)
            if hit is not None:
                return RankRecord("naver", "ad", kw, pos, len(units),
                                  section=section, matched=hit[:200])
        return RankRecord("naver", "ad", kw, None, len(units), status="not_found")

    @staticmethod
    def _rank_organic(kw: str, blocks: list[tuple[str, object]]) -> RankRecord:
        if not blocks:
            # 페이지는 왔는데 결과 블록 0개 — 구조 변경 신호
            return RankRecord("naver", "organic", kw, None, 0, status="parse_fail",
                              detail="결과 블록 0개 추출")
        scan = blocks[:config.ORGANIC_SCAN_LIMIT]
        for pos, (section, blk) in enumerate(scan, start=1):
            hit = next((a["href"] for a in blk.select("a[href]")
                        if config.is_self_url(a["href"])), None)
            if hit is not None:
                return RankRecord("naver", "organic", kw, pos, len(scan),
                                  section=section, matched=hit[:200])
        return RankRecord("naver", "organic", kw, None, len(scan), status="not_found")
