"""구글 SERP 로컬 브라우저 수집 — 거주지 IP + 실제 Chromium 으로 무료 우회.

GitHub Actions 데이터센터 IP 는 구글이 무조건 reCAPTCHA 를 띄우지만(collectors/
google.py 도킹스트링 실측), 거주지 IP 의 실제 브라우저는 광고·오가닉이 모두
정상 렌더링된다(2026-08-07 실측). 이 스크립트는 사무실/집 PC 에서 매일 07:00
작업 스케줄러로 돌며:

  1) Playwright 헤드리스 Chromium 으로 전체 키워드 SERP 를 렌더링
  2) 기존 파서(collectors.google._parse_serp)로 광고/오가닉 순위 추출
  3) data/google_serp.json 저장 → google-serp 브랜치에 force-push
     (state 브랜치와 동일한 '매회 단일 커밋 교체' 패턴)

07:30 daily Actions 런의 구글 수집기가 이 JSON(오늘자일 때만)을 소비한다.
PC 가 꺼져 인박스가 없으면 Actions 는 기존대로 blocked 를 기록한다 — 어제
데이터가 오늘로 복제되는 것보다 결측이 정직하다.

사전 준비(1회):
  pip install playwright && playwright install chromium

실행:
  python scripts/collect_google_local.py                  # 전체 수집 + push
  python scripts/collect_google_local.py --no-push        # 수집·저장만
  python scripts/collect_google_local.py --keywords 삼삼엠투,단기임대 --dry-run
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import config                                    # noqa: E402
import keywords as kwmod                         # noqa: E402
from collectors.base import (                    # noqa: E402
    RankRecord, now_kst, polite_sleep, today_kst,
)
from collectors.google import (                  # noqa: E402
    _BLOCK_MARKERS, _blocked_pair, _error_pair, _parse_serp,
)

_SERP_URL = "https://www.google.com/search?q={q}&hl=ko&gl=kr&num=20"
_INBOX_BRANCH = "google-serp"


# ── 브라우저 수집 ────────────────────────────────────
def _fetch_rendered(page, kw: str) -> tuple[str, str, str]:
    """(html, block_detail, error) — collectors.google._fetch 와 동일 계약."""
    from urllib.parse import quote
    try:
        page.goto(_SERP_URL.format(q=quote(kw)),
                  wait_until="domcontentloaded",
                  timeout=config.REQUEST_TIMEOUT * 1000)
        if "/sorry/" in page.url:
            return "", "sorry_redirect", ""
        # 결과 컨테이너 대기 → 광고는 비동기 주입이라 잠깐 더 기다린다
        page.wait_for_selector("#search, #rso", timeout=15_000)
        page.wait_for_timeout(1_500)
        html = page.content()
    except Exception as e:  # noqa: BLE001 — 키워드 단위 격리
        return "", "", f"browser: {type(e).__name__}: {e}"
    low = html.lower()
    if any(m in low or m in html for m in _BLOCK_MARKERS):
        return "", "captcha", ""
    return html, "", ""


def collect(kw_list: list[str]) -> list[RankRecord]:
    from playwright.sync_api import sync_playwright

    records: list[RankRecord] = []
    consecutive_blocks = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            locale="ko-KR", timezone_id="Asia/Seoul",
            viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        for i, kw in enumerate(kw_list):
            if i:
                polite_sleep(config.GOOGLE_SLEEP_BASE)
            html, block_detail, err = _fetch_rendered(page, kw)
            if err:
                records += _error_pair(kw, err)
                print(f"  [{i + 1}/{len(kw_list)}] {kw}: error {err}")
                continue
            if block_detail:
                consecutive_blocks += 1
                records += _blocked_pair(kw, block_detail)
                print(f"  [{i + 1}/{len(kw_list)}] {kw}: BLOCKED {block_detail}")
                if consecutive_blocks >= config.GOOGLE_BLOCK_ABORT:
                    # 거주지 IP 도 차단이 이어지면 중단 — IP 평판 보호가 우선
                    for rest in kw_list[i + 1:]:
                        records += _blocked_pair(rest, "aborted")
                    break
                continue
            consecutive_blocks = 0
            pair = _parse_serp(kw, html)
            records += pair
            ad, org = pair
            print(f"  [{i + 1}/{len(kw_list)}] {kw}: "
                  f"ad={ad.rank}/{ad.total}({ad.status}) "
                  f"organic={org.rank}/{org.total}({org.status})")
        browser.close()
    return records


# ── 저장 + push ──────────────────────────────────────
def save_inbox(records: list[RankRecord]) -> Path:
    doc = {
        "date": today_kst(),
        "generated_at": now_kst(),
        "source": "local_browser",
        "records": [asdict(r) for r in records],
    }
    path = config.GOOGLE_INBOX_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return path


def push_inbox(path: Path) -> None:
    """google-serp 브랜치에 단일 커밋으로 교체 push — collect.yml state 패턴."""
    origin = subprocess.run(
        ["git", "-C", str(_ROOT), "config", "--get", "remote.origin.url"],
        capture_output=True, text=True, check=True).stdout.strip()
    tmp = Path(tempfile.mkdtemp(prefix="krm-google-"))
    try:
        (tmp / "data").mkdir()
        shutil.copy(path, tmp / "data" / path.name)

        def git(*args: str) -> None:
            subprocess.run(["git", "-C", str(tmp), *args], check=True,
                           capture_output=True, text=True)

        git("init", "-qb", _INBOX_BRANCH)
        git("add", "-A")
        git("-c", "user.name=krm-local", "-c",
            "user.email=krm-local@users.noreply.github.com",
            "commit", "-qm", f"google inbox: {now_kst()}")
        git("push", "-q", "--force", origin,
            f"{_INBOX_BRANCH}:{_INBOX_BRANCH}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="구글 SERP 로컬 브라우저 수집")
    ap.add_argument("--keywords", help="쉼표 구분 임시 키워드(기본: 전체 240개)")
    ap.add_argument("--dry-run", action="store_true", help="출력만, 저장/push 없음")
    ap.add_argument("--no-push", action="store_true", help="JSON 저장까지만")
    args = ap.parse_args()

    if args.keywords:
        kw_list = [k.strip() for k in args.keywords.split(",") if k.strip()]
    else:
        kw_list = [k for k, _ in kwmod.all_keywords()]

    print(f"구글 로컬 수집 시작: {len(kw_list)}개 키워드 ({now_kst()})")
    records = collect(kw_list)
    blocked = sum(1 for r in records if r.status == "blocked")
    errors = sum(1 for r in records if r.status == "error")
    print(f"완료: {len(records)}건 (blocked {blocked} · error {errors})")

    if args.dry_run:
        return 1 if errors or blocked else 0
    path = save_inbox(records)
    print(f"저장: {path}")
    if not args.no_push:
        push_inbox(path)
        print(f"push: origin/{_INBOX_BRANCH}")
    return 1 if errors or blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
