"""다음(Daum) 통합검색 SERP 수집기 — 광고/오가닉 영역별 자사 순위 측정.

SERP 구조(2026-08 실측): 결과 컬렉션은 전부 div[disp-attr] 블록이며 두 세대 마크업이
공존한다 — 레거시(ul.list_ad, strong.tit-g a[href])와 cubic 웹컴포넌트(<template> 안의
c-title/c-link[data-href]). 광고 유닛(파워링크·프리미엄링크·스페셜링크)은 마크업 세대와
무관하게 내부 컨테이너가 ad_sch 클래스를 달고 있고, 아이템은 div.c-item-ad 로 통일되어
있어 이 두 가지를 판별 기준으로 쓴다(disp-attr 코드는 키워드마다 0KL/0NL/0LL/0SC/0FL
등으로 바뀌므로 코드에 의존하지 않는다).

광고 자사 판별 주의: 파워링크(네이버 신디케이션) 광고의 href 는 불투명한
ader.naver.com 리다이렉트라 랜딩 도메인이 없고, 실제 랜딩 URL 은 onclick 의
smartLog d= 파라미터에 퍼센트인코딩으로 들어 있다(%2F%2F33m2.co.kr — 도메인 문자열은
그대로 보존됨). 그래서 href 와 onclick 을 합쳐 is_self_url 에 넘긴다.
"""
from __future__ import annotations

import re
import urllib.parse

from bs4 import BeautifulSoup

import config
from collectors.base import BaseCollector, CollectResult, RankRecord, make_session, polite_sleep

SEARCH_URL = "https://search.daum.net/search"

# 오가닉 아이템 타이틀 셀렉터 — 컬렉션(바로가기/통합웹/사이트/뉴스/동영상/쇼핑/책)마다
# 마크업이 다르지만 '아이템당 타이틀 요소 1개' 로 수렴하는 조합. 실측: 삼삼엠투 SERP
# 에서 0DL=1·TWA=6·DNS=4·SNP=1·VOI=6·LB2=1·IVR=3 개로 중복 없이 추출됨.
_ORGANIC_ITEM_SEL = (
    'c-title[data-href], c-link[slot="title"][data-href], '
    "strong.tit-g a[href], a.link_info[href]"
)

# 관련검색어·실시간트렌드류 사이드 블록 — 결과가 아니므로 오가닉 스캔에서 제외.
_NON_RESULT_CLASSES = ("section_related", "content_keyword", "content_realtime")

_BLOCK_MARKERS = ("captcha", "자동 입력 방지", "자동입력 방지", "비정상적인 검색")

_D_PARAM = re.compile(r"d=([^&'\"]+)")


def _block_label(block) -> str:
    """컬렉션 블록의 사람이 읽는 이름 — 파워링크/통합웹/사이트 등."""
    t = block.select_one("h2.tit")
    if t and t.get_text(strip=True):
        return t.get_text(strip=True)
    hc = block.select_one("c-header-collection[data-title]")
    if hc and hc.get("data-title"):
        return hc["data-title"]
    t = block.select_one("h2.screen_out")
    if t and t.get_text(strip=True):
        return t.get_text(strip=True)
    return block.get("disp-attr", "")


def _is_ad_block(block) -> bool:
    inner = block.find("div")
    return inner is not None and "ad_sch" in (inner.get("class") or [])


def _is_non_result_block(block) -> bool:
    inner = block.find("div")
    cls = (inner.get("class") or []) if inner else []
    return any(c in _NON_RESULT_CLASSES for c in cls)


def _ad_landing_url(anchor) -> str:
    """광고 앵커의 실제 랜딩 URL — 불투명 리다이렉트면 onclick d= 에서 복원."""
    href = anchor.get("href") or ""
    m = _D_PARAM.search(anchor.get("onclick") or "")
    if m and "%2F%2F" in m.group(1):  # URL 형태의 d= 만 랜딩으로 취급
        return urllib.parse.unquote(m.group(1))
    return href


class DaumRankCollector(BaseCollector):
    key = "daum"
    label = "다음 통합검색 순위"

    def __init__(self) -> None:
        self.session = make_session()

    def collect(self, keywords: list[str]) -> CollectResult:
        records: list[RankRecord] = []
        for i, kw in enumerate(keywords):
            if i:
                polite_sleep(config.SLEEP_BASE)
            records.extend(self._collect_one(kw))
        return CollectResult(records=records, meta={"engine": self.key})

    def _collect_one(self, kw: str) -> list[RankRecord]:
        try:
            resp = self.session.get(
                SEARCH_URL, params={"w": "tot", "q": kw},
                timeout=config.REQUEST_TIMEOUT)
        except Exception as e:  # noqa: BLE001 — 네트워크 계열 전부 error 로 기록
            return [
                RankRecord(self.key, area, kw, None, 0, status="error", detail=str(e)[:200])
                for area in ("ad", "organic")
            ]

        if resp.status_code in (403, 429, 503):
            return [
                RankRecord(self.key, area, kw, None, 0, status="blocked",
                           detail=f"HTTP {resp.status_code}")
                for area in ("ad", "organic")
            ]
        if resp.status_code != 200:
            return [
                RankRecord(self.key, area, kw, None, 0, status="error",
                           detail=f"HTTP {resp.status_code}")
                for area in ("ad", "organic")
            ]
        low = resp.text[:3000].lower()
        if any(m in low for m in _BLOCK_MARKERS):
            return [
                RankRecord(self.key, area, kw, None, 0, status="blocked",
                           detail="차단 페이지 마커 감지")
                for area in ("ad", "organic")
            ]

        soup = BeautifulSoup(resp.text, "lxml")
        blocks = soup.select("[disp-attr]")
        if not blocks:
            # 200 인데 컬렉션 블록 자체가 없음 — SERP 구조 변경 의심
            return [
                RankRecord(self.key, area, kw, None, 0, status="parse_fail",
                           detail="disp-attr 블록 0개")
                for area in ("ad", "organic")
            ]
        return [self._scan_ads(kw, blocks), self._scan_organic(kw, blocks)]

    def _scan_ads(self, kw: str, blocks) -> RankRecord:
        """프리미엄링크(카카오 키워드광고) 유닛만 노출 순서대로 순번.

        다음 SERP 의 '파워링크' 섹션은 네이버 검색광고가 제휴 매체로 신디케이션된
        영역이라 카카오 광고 성과와 무관 — 순위 팔로업 대상에서 제외(사용자 확정
        2026-08-06). 스페셜링크 등 그 외 광고 섹션도 프리미엄링크가 아니므로 제외.
        광고 유닛 0개 = no_section 은 이제 '프리미엄링크 영역 없음'을 뜻한다.
        """
        rank = 0
        found: RankRecord | None = None
        for block in blocks:
            if not _is_ad_block(block):
                continue
            section = _block_label(block)
            if "프리미엄링크" not in section:
                continue  # 파워링크(네이버 신디케이션)·스페셜링크 등
            for item in block.select("div.c-item-ad"):
                a = item.select_one("strong.tit-g a[href]") or item.find("a", href=True)
                if a is None:
                    continue
                rank += 1
                if found is None:
                    href = a.get("href") or ""
                    onclick = a.get("onclick") or ""
                    if config.is_self_url(href) or config.is_self_url(onclick):
                        found = RankRecord(
                            self.key, "ad", kw, rank, 0,
                            section=section, matched=_ad_landing_url(a)[:300])
        if rank == 0:
            # 광고 유닛 자체가 SERP 에 없음(브랜드 키워드에선 흔함) — 정상 케이스
            return RankRecord(self.key, "ad", kw, None, 0, status="no_section")
        if found is None:
            return RankRecord(self.key, "ad", kw, None, rank, status="not_found")
        found.total = rank
        return found

    def _scan_organic(self, kw: str, blocks) -> RankRecord:
        # 오가닉 순위 = 일반 웹문서 컬렉션('통합웹'·'사이트') 내 순번만. 뉴스·동영상·
        # 쇼핑·책·이미지 등 버티컬은 제외한다(2026-08-30 사용자 리포트: 웹 결과가 아닌
        # 버티컬 아이템까지 세어 실제 화면·랭크트래커와 순위가 어긋남). 네이버와 동일 원칙.
        WEB_SECTIONS = ("통합웹", "사이트")
        rank = 0
        found: RankRecord | None = None
        total_items = 0
        for block in blocks:
            if _is_ad_block(block) or _is_non_result_block(block):
                continue
            section = _block_label(block)
            items = block.select(_ORGANIC_ITEM_SEL)
            total_items += len(items)
            if section not in WEB_SECTIONS:
                continue
            for item in items:
                if rank >= config.ORGANIC_SCAN_LIMIT:
                    break
                rank += 1
                if found is None:
                    url = item.get("data-href") or item.get("href") or ""
                    if config.is_self_url(url):
                        found = RankRecord(
                            self.key, "organic", kw, rank, 0,
                            section=section, matched=url[:300])
            if rank >= config.ORGANIC_SCAN_LIMIT:
                break
        if total_items == 0:
            # 통합검색 결과 아이템 0개 = 구조 변경 신호(웹 컬렉션 부재와 구분)
            return RankRecord(self.key, "organic", kw, None, 0, status="parse_fail",
                              detail="오가닉 아이템 0개 추출")
        if found is None:
            return RankRecord(self.key, "organic", kw, None, rank, status="not_found")
        found.total = rank
        return found
