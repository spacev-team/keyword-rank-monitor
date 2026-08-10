"""오케스트레이터 — 엔진별 수집 → SQLite 적재(+선택 Sheets 미러) → 변화 알림.

사용 예:
  python run.py --all                        # 5개 엔진 전체 키워드
  python run.py --engines naver,daum         # 특정 엔진만
  python run.py --group brand                # 브랜드 키워드만
  python run.py --keywords 삼삼엠투,단기임대   # 임시 키워드(스모크 테스트)
  python run.py --all --dry-run              # 저장/알림 없이 stdout 확인
  python run.py --export-csv out.csv         # 이력 CSV 내보내기(수집 없음)
"""
from __future__ import annotations

import argparse
import datetime as _dt
import traceback

import config
import keywords as kwmod
from collectors.base import KST, RankRecord, now_kst, today_kst
from notify import google_chat
from store import Store, sheets_append

# key → (모듈, 클래스). SERP 3종 + 앱마켓 2종.
REGISTRY: dict[str, tuple[str, str]] = {
    "naver": ("collectors.naver", "NaverRankCollector"),
    "google": ("collectors.google", "GoogleRankCollector"),
    "daum": ("collectors.daum", "DaumRankCollector"),
    "playstore": ("collectors.playstore", "PlayStoreRankCollector"),
    "appstore": ("collectors.appstore", "AppStoreRankCollector"),
}

# 스케줄 모드 — 구글은 봇차단 민감·SerpAPI 비용 문제로 하루 1회만(사용자 확정 2026-08-06).
#   core  = 하루 3회(구글 제외) / daily = 하루 1회(구글 포함, 아침 첫 런)
MODES: dict[str, list[str]] = {
    "core": ["naver", "daum", "playstore", "appstore"],
    "daily": ["naver", "google", "daum", "playstore", "appstore"],
}


def _load(key: str):
    import importlib
    mod_path, cls = REGISTRY[key]
    return getattr(importlib.import_module(mod_path), cls)()


def _summary_lines(records: list[RankRecord], kw_groups: dict[str, str]) -> list[str]:
    """엔진×영역별 요약: 브랜드/일반 각각 노출 커버리지와 상위권 수."""
    from collections import defaultdict
    agg: dict[tuple[str, str, str], list[RankRecord]] = defaultdict(list)
    for r in records:
        grp = "브랜드" if kw_groups.get(r.keyword, "").startswith("brand") else "일반"
        agg[(r.engine, r.area, grp)].append(r)
    lines = []
    for (eng, area, grp), rs in sorted(agg.items()):
        found = [r for r in rs if r.rank is not None]
        top3 = sum(1 for r in found if r.rank <= 3)
        bad = sum(1 for r in rs if r.status in ("blocked", "parse_fail", "error"))
        line = (f"[{eng}/{area}] {grp}: 노출 {len(found)}/{len(rs)}"
                f" · TOP3 {top3}")
        if bad:
            line += f" · ⚠️수집이상 {bad}"
        lines.append(line)
    return lines


def _change_alerts(records: list[RankRecord], prev: dict, kw_groups: dict[str, str]) -> list[str]:
    """직전 런 대비 악화만 알림(개선은 요약에 반영) — 노이즈 억제."""
    alerts = []
    for r in records:
        if r.status in ("blocked", "error"):
            continue  # 수집 이상은 요약의 ⚠️ 로만 — 순위 변화로 오독 방지
        p = prev.get((r.engine, r.area, r.keyword))
        if not p:
            continue
        p_rank, p_status = p
        is_brand = kw_groups.get(r.keyword, "").startswith("brand")
        if p_rank is not None and r.rank is None and p_status == "ok":
            alerts.append(f"🔻 [{r.engine}/{r.area}] '{r.keyword}' 노출 이탈 (직전 {p_rank}위)")
        elif p_rank is not None and r.rank is not None:
            if r.rank - p_rank >= config.ALERT_ORGANIC_DROP:
                alerts.append(f"🔻 [{r.engine}/{r.area}] '{r.keyword}' {p_rank}위 → {r.rank}위")
            elif is_brand and p_rank <= config.ALERT_TOP_RANK < r.rank:
                alerts.append(f"⚠️ [{r.engine}/{r.area}] 브랜드 '{r.keyword}' "
                              f"TOP{config.ALERT_TOP_RANK} 이탈 ({p_rank}→{r.rank}위)")
    return alerts


def run(engine_keys: list[str], kw_pairs: list[tuple[str, str]], dry_run: bool,
        with_volumes: bool = False) -> int:
    kw_groups = dict(kw_pairs)
    kws = [k for k, _ in kw_pairs]
    run_id = _dt.datetime.now(KST).strftime("%Y%m%d-%H%M%S")
    collected_at, date = now_kst(), today_kst()
    all_records: list[RankRecord] = []
    errors: list[str] = []

    for key in engine_keys:
        # 구글 Serper 경로는 크레딧 과금이라 전체 240개는 낭비 →
        # 브랜드 계열만(기본 brand,brand_ext,adhoc ≈ 69개, KRM_GOOGLE_GROUPS 로 조정).
        if key == "google" and config.SERPER_KEY and not config.SERPAPI_KEY:
            engine_kws = [k for k, g in kw_pairs if g in config.GOOGLE_KW_GROUPS]
        else:
            engine_kws = kws
        try:
            col = _load(key)
            res = col.collect(engine_kws)
            all_records.extend(res.records)
            print(f"[{key}] {len(res.records)}건 · meta={res.meta}")
        except Exception:
            errors.append(f"[{key}] 수집기 크래시:\n{traceback.format_exc(limit=3)}")
            print(errors[-1])

    if dry_run:
        for r in all_records:
            print(f"  {r.engine:9s} {r.area:7s} {r.keyword:20s} "
                  f"rank={r.rank} total={r.total} {r.status} {r.section} {r.matched[:60]}")
        return 1 if errors else 0

    store = Store()
    prev = store.previous_run_map(run_id)
    n = store.append(run_id, collected_at, date, all_records, kw_groups)
    print(f"SQLite 적재 {n}건 (run_id={run_id})")

    try:
        m = sheets_append(all_records, run_id, collected_at, date, kw_groups)
        if m:
            print(f"Sheets 미러 {m}건")
    except Exception as exc:  # Sheets 실패는 수집 성공을 깨지 않는다
        errors.append(f"[sheets] {exc}")
        print(errors[-1])

    # 검색량(키워드도구 30일) — daily 런에서 하루 1회. 실패해도 순위 수집은 유효.
    if with_volumes:
        try:
            import volumes
            meta = volumes.collect_and_store(store.db, [k for k, _ in kw_pairs])
            print(f"[volumes] meta={meta}")
        except Exception:
            errors.append(f"[volumes] 검색량 수집 실패:\n{traceback.format_exc(limit=3)}")
            print(errors[-1])

    lines = _summary_lines(all_records, kw_groups)
    alerts = _change_alerts(all_records, prev, kw_groups)
    body = [f"📊 키워드 순위 수집 완료 ({collected_at})", *lines]
    if alerts:
        body += ["", "— 순위 변화 —", *alerts[:40]]  # 폭주 방지 상한
    if errors:
        body += ["", "— 오류 —", *errors]
    google_chat(config.GOOGLE_CHAT_WEBHOOK_URL, "\n".join(body))
    print("\n".join(lines))
    store.close()
    return 1 if errors else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="키워드 순위 모니터링")
    ap.add_argument("--all", action="store_true", help="전체 엔진 실행")
    ap.add_argument("--engines", help="쉼표 구분: " + ",".join(REGISTRY))
    ap.add_argument("--mode", choices=list(MODES),
                    help="스케줄 모드: core(구글 제외, 1일 3회) | daily(구글 포함, 1일 1회)")
    ap.add_argument("--group", choices=["brand", "generic", "all"], default="all",
                    help="키워드 그룹 필터(brand = brand+brand_ext)")
    ap.add_argument("--keywords", help="쉼표 구분 임시 키워드(그룹 필터 무시)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--volumes", action="store_true",
                    help="네이버 검색량(30일)도 수집 — daily 모드는 자동 포함")
    ap.add_argument("--export-csv", metavar="PATH", help="이력 CSV 내보내기 후 종료")
    args = ap.parse_args()

    if args.export_csv:
        from pathlib import Path
        n = Store().export_csv(Path(args.export_csv))
        print(f"{n}행 → {args.export_csv}")
        return 0

    if args.mode:
        keys = MODES[args.mode]
        # 구글 요일 게이트(스케줄 모드 한정) — 브랜드 키워드는 구글 1~2위로
        # 안정적이라 주 1회로 충분, API 크레딧을 아낀다(KRM_GOOGLE_DAYS 로 조정,
        # 빈 값 = 매일). --engines 명시 실행은 게이트를 우회한다.
        today = _dt.datetime.now(KST).strftime("%a").lower()
        if "google" in keys and config.GOOGLE_DAYS and today not in config.GOOGLE_DAYS:
            keys = [k for k in keys if k != "google"]
            print(f"구글 수집 생략: 오늘({today}) ∉ KRM_GOOGLE_DAYS{sorted(config.GOOGLE_DAYS)}")
    elif args.engines:
        keys = [k.strip() for k in args.engines.split(",") if k.strip()]
        unknown = [k for k in keys if k not in REGISTRY]
        if unknown:
            ap.error(f"알 수 없는 엔진: {unknown}")
    elif args.all:
        keys = list(REGISTRY)
    else:
        ap.error("--mode, --all 또는 --engines 필요")

    if args.keywords:
        pairs = [(k.strip(), "adhoc") for k in args.keywords.split(",") if k.strip()]
    else:
        pairs = kwmod.all_keywords()
        if args.group == "brand":
            pairs = [(k, g) for k, g in pairs if g.startswith("brand")]
        elif args.group == "generic":
            pairs = [(k, g) for k, g in pairs if g == "generic"]

    with_volumes = args.volumes or args.mode == "daily"
    print(f"엔진 {keys} × 키워드 {len(pairs)}개" + (" + 검색량" if with_volumes else ""))
    return run(keys, pairs, args.dry_run, with_volumes=with_volumes)


if __name__ == "__main__":
    raise SystemExit(main())
