"""전역 설정 — 자사 판별 타깃·수집 파라미터·자격증명(.env) 로딩.

external-metrics-monitor 와 동일한 .env 로딩 컨벤션. 이 프로젝트는 독립 실행이며
저장은 로컬 SQLite(기본) + 선택적 Google Sheets, 알림은 선택적 Google Chat.
"""
from __future__ import annotations

import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parent


def _load_dotenv(path: Path = _ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


_load_dotenv()


def env(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, default)


# ── 자사 판별 타깃 ───────────────────────────────────
#   웹 SERP: 결과 링크의 호스트가 아래 도메인(서브도메인 포함)이면 자사.
#   앱스토어 상세 링크(SERP 내 노출)도 자사로 인정.
SELF_NAME = "삼삼엠투"
SELF_DOMAINS = tuple(
    d.strip() for d in env("KRM_SELF_DOMAINS", "33m2.co.kr").split(",") if d.strip())
SELF_PLAY_PKG = env("KRM_PLAY_PKG", "com.samsamm2.mobileapp")
SELF_IOS_ID = env("KRM_IOS_ID", "1491007143")

APP_URL_MARKERS = (
    f"play.google.com/store/apps/details?id={SELF_PLAY_PKG}",
    f"id{SELF_IOS_ID}",
)


def is_self_url(url: str) -> bool:
    """SERP 결과 URL 자사 판별 — 도메인 서픽스 매칭 + 앱 상세 링크."""
    if not url:
        return False
    u = url.lower()
    for marker in APP_URL_MARKERS:
        if marker.lower() in u:
            return True
    # 호스트 추출 없이 서픽스 매칭하면 경로에 도메인이 든 리다이렉트 URL 도 잡힌다
    # (네이버 광고 클릭 URL 은 ader.naver.com/...&url=https%3A%2F%2F33m2.co.kr — 의도된 동작).
    return any(d in u for d in SELF_DOMAINS)


# ── 수집 파라미터 ────────────────────────────────────
ORGANIC_SCAN_LIMIT = int(env("KRM_ORGANIC_LIMIT", "20") or 20)   # 오가닉 상위 N개까지 스캔
PLAY_HITS = 30            # google_play_scraper.search 첫 페이지 최대
ITUNES_LIMIT = 200
REQUEST_TIMEOUT = 20
SLEEP_BASE = float(env("KRM_SLEEP_BASE", "2.0") or 2.0)          # 키워드 간 대기(+지터)
GOOGLE_SLEEP_BASE = float(env("KRM_GOOGLE_SLEEP", "6.0") or 6.0)  # 구글은 차단 민감 → 길게
GOOGLE_BLOCK_ABORT = 5    # 연속 차단 N회 → 해당 런의 구글 수집 중단(무의미한 재시도 방지)

# ── 저장/알림(선택) ──────────────────────────────────
DB_PATH = _ROOT / "data" / "rank_history.sqlite"
SHEET_ID = env("KRM_SHEET_ID")                       # 미설정 시 Sheets 적재 생략
SERVICE_ACCOUNT = env("GOOGLE_APPLICATION_CREDENTIALS", "./secrets/service-account.json")
GOOGLE_CHAT_WEBHOOK_URL = env("KRM_GOOGLE_CHAT_WEBHOOK_URL") or env("GOOGLE_CHAT_WEBHOOK_URL")
SERPAPI_KEY = env("SERPAPI_KEY")                     # 설정 시 구글 수집이 SerpAPI 경유(차단 회피)

# ── 알림 임계값 ──────────────────────────────────────
ALERT_ORGANIC_DROP = 5    # 오가닉 순위 N계단 이상 하락 시 알림
ALERT_TOP_RANK = 3        # 브랜드 키워드가 이 순위 밖으로 밀리면 알림
