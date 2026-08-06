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
    run_ids = [r[0] for r in db.execute(
        "SELECT DISTINCT run_id FROM ranks ORDER BY run_id DESC LIMIT 2")]
    records: list[dict] = []
    if run_ids:
        latest = run_ids[0]
        prev_map: dict[tuple, int | None] = {}
        if len(run_ids) > 1:
            # 직전 런에 없던 (engine,area,keyword)는 prev 없음 — 엔진별 주기가 달라
            # (구글 1일 1회) 최신 core 런과 비교 대상이 어긋날 수 있어 엔진별 직전 런을 찾는다.
            for eng, area, kw, rank in db.execute(
                    "SELECT engine, area, keyword, rank FROM ranks WHERE run_id = ("
                    "  SELECT MAX(run_id) FROM ranks r2 WHERE r2.engine = ranks.engine"
                    "  AND r2.run_id < ?) AND run_id < ?", (latest, latest)):
                prev_map[(eng, area, kw)] = rank
        for (eng, area, kw, rank, total, section, matched, status, ca) in db.execute(
                "SELECT engine, area, keyword, rank, total, section, matched, status,"
                " collected_at FROM ranks WHERE run_id = ?", (latest,)):
            records.append({
                "engine": eng, "area": area, "keyword": kw,
                "group": kw_groups.get(kw, "generic"),
                "rank": rank, "prev_rank": prev_map.get((eng, area, kw)),
                "total": total, "section": section, "matched": matched,
                "status": status, "collected_at": ca,
            })
    # 엔진별 최신 런이 다를 수 있으므로(구글은 daily 런에만 존재) 최신 core 런에 없는
    # 엔진은 그 엔진의 마지막 런 레코드로 보강한다 — 대시보드에서 구글이 비지 않도록.
    have = {r["engine"] for r in records}
    for (eng,) in db.execute("SELECT DISTINCT engine FROM ranks"):
        if eng in have:
            continue
        for (eng2, area, kw, rank, total, section, matched, status, ca) in db.execute(
                "SELECT engine, area, keyword, rank, total, section, matched, status,"
                " collected_at FROM ranks WHERE engine = ? AND run_id ="
                " (SELECT MAX(run_id) FROM ranks WHERE engine = ?)", (eng, eng)):
            records.append({
                "engine": eng2, "area": area, "keyword": kw,
                "group": kw_groups.get(kw, "generic"),
                "rank": rank, "prev_rank": None,
                "total": total, "section": section, "matched": matched,
                "status": status, "collected_at": ca,
            })

    latest_doc = {"generated_at": now_kst(), "run_id": run_ids[0] if run_ids else None,
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
    # 하루의 대표값 = 그날 마지막 런(run_id 사전순 = 시간순)의 rank
    for eng, area, kw, date, rank in db.execute(
            "SELECT engine, area, keyword, date, rank FROM ranks r "
            "WHERE run_id = (SELECT MAX(run_id) FROM ranks r2 WHERE r2.engine = r.engine"
            "  AND r2.area = r.area AND r2.keyword = r.keyword AND r2.date = r.date) "
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
