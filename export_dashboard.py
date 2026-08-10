"""대시보드 데이터 내보내기 — SQLite → docs/data/*.json (GitHub Pages 정적 대시보드용).

Actions 러너가 수집 직후 실행 → docs/ 전체가 Pages 아티팩트로 배포된다.
- latest.json: 최신 런 전체 레코드 + 직전 런 순위(prev_rank) — 현재 상태·변동 표시용
- trends.json: 일자별 시계열(하루 여러 런이면 마지막 런) 최근 N일 — 스파크라인/차트용
JSON 은 사람이 아닌 대시보드 JS 가 소비 — 스키마 변경 시 docs/app.js 와 함께 바꿀 것.
"""
from __future__ import annotations

import json
from pathlib import Path

import config
import keywords as kwmod
from collectors.base import now_kst
from store import Store

OUT_DIR = Path(__file__).resolve().parent / "docs" / "data"
TREND_DAYS = 90


def export(store: Store, out_dir: Path = OUT_DIR) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    db = store.db
    kw_groups = dict(kwmod.all_keywords())

    # ── latest.json ──────────────────────────────────
    # 엔진별 '마지막 정상 런' 기준(2026-08-06 실사고: 오후 런에서 네이버가 러너 IP 를
    # 차단해 478/480 blocked — 최신 런을 그대로 쓰면 차단 런이 아침의 정상 데이터를
    # 대시보드에서 덮어쓴다). 정상 런 = 해당 엔진 blocked 비율 < 50%. 하나도 없으면
    # 최신 런(차단 상태 그대로 노출 — 구글 SerpAPI 미설정처럼 상시 차단인 엔진 커버).
    # 레코드마다 collected_at 이 있어 대시보드에서 어느 시점 데이터인지 드러난다.
    def _engine_runs(eng: str) -> list[str]:
        """정상 런 우선 정렬: [마지막 정상, 그 직전 정상, ...] 없으면 [최신 런]."""
        rows = db.execute(
            "SELECT run_id, SUM(status = 'blocked'), COUNT(*) FROM ranks "
            "WHERE engine = ? GROUP BY run_id ORDER BY run_id DESC", (eng,)).fetchall()
        good = [rid for rid, b, n in rows if b * 2 < n]
        return good if good else [rows[0][0]]

    # 네이버 검색량(키워드도구 30일 총검색수) — daily 런이 volumes 테이블에 적재.
    # 레코드마다 sv 로 조인(엔진 무관 동일 값). 없으면 키 생략 → 대시보드는 '–'.
    import volumes as vmod
    sv_map = vmod.latest_map(db)

    records: list[dict] = []
    latest_run = None
    for (eng,) in db.execute("SELECT DISTINCT engine FROM ranks"):
        runs = _engine_runs(eng)
        chosen = runs[0]
        latest_run = max(latest_run or chosen, chosen)
        prev_map: dict[tuple, int | None] = {}
        if len(runs) > 1:
            for area, kw, rank in db.execute(
                    "SELECT area, keyword, rank FROM ranks WHERE engine = ? AND run_id = ?",
                    (eng, runs[1])):
                prev_map[(area, kw)] = rank
        for (area, kw, rank, total, section, matched, status, ca) in db.execute(
                "SELECT area, keyword, rank, total, section, matched, status, collected_at"
                " FROM ranks WHERE engine = ? AND run_id = ?", (eng, chosen)):
            rec = {
                "engine": eng, "area": area, "keyword": kw,
                "group": kw_groups.get(kw, "generic"),
                "rank": rank, "prev_rank": prev_map.get((area, kw)),
                "total": total, "section": section, "matched": matched,
                "status": status, "collected_at": ca,
            }
            if kw in sv_map:
                rec["sv"] = sv_map[kw]  # 최근 30일 총검색수(PC+MO)
            records.append(rec)

    latest_doc = {"generated_at": now_kst(), "run_id": latest_run,
                  "self_name": config.SELF_NAME, "records": records}
    (out_dir / "latest.json").write_text(
        json.dumps(latest_doc, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")

    # ── trends.json ──────────────────────────────────
    days = [r[0] for r in db.execute(
        "SELECT DISTINCT date FROM ranks ORDER BY date DESC LIMIT ?", (TREND_DAYS,))]
    days.reverse()
    day_idx = {d: i for i, d in enumerate(days)}
    series: dict[str, list] = {}
    # 하루의 대표값 = 그날 마지막 '측정' 런의 rank — blocked/error/parse_fail 은
    # 측정이 아니므로 제외(차단 런이 그날의 정상 측정치를 null 로 덮지 않게).
    for eng, area, kw, date, rank in db.execute(
            "SELECT engine, area, keyword, date, rank FROM ranks r "
            "WHERE status IN ('ok','not_found','no_section') "
            "AND run_id = (SELECT MAX(run_id) FROM ranks r2 WHERE r2.engine = r.engine"
            "  AND r2.area = r.area AND r2.keyword = r.keyword AND r2.date = r.date"
            "  AND r2.status IN ('ok','not_found','no_section')) "
            "AND date >= ?", (days[0] if days else "",)):
        key = f"{eng}|{area}|{kw}"
        if key not in series:
            series[key] = [None] * len(days)
        series[key][day_idx[date]] = rank

    trends_doc = {"generated_at": now_kst(), "days": days, "series": series}
    (out_dir / "trends.json").write_text(
        json.dumps(trends_doc, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")
    return {"records": len(records), "days": len(days), "series": len(series)}


if __name__ == "__main__":
    print(export(Store()))
