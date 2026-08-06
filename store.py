"""저장 계층 — SQLite(기본) + 선택적 Google Sheets 누적.

SQLite 가 진실 원본(로컬 쿼리·전회차 비교·CSV 내보내기), Sheets 는 팀 공유용 미러.
Sheets 미설정/실패가 수집 자체를 죽이지 않는다.
"""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

import config
from collectors.base import RankRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ranks (
    collected_at TEXT NOT NULL,     -- YYYY-MM-DD HH:MM:SS (KST)
    date         TEXT NOT NULL,     -- YYYY-MM-DD
    run_id       TEXT NOT NULL,     -- 실행 단위(같은 런의 레코드 묶음)
    engine       TEXT NOT NULL,
    area         TEXT NOT NULL,     -- ad|organic|app
    keyword      TEXT NOT NULL,
    kw_group     TEXT NOT NULL,     -- brand|brand_ext|generic
    rank         INTEGER,           -- NULL = 미노출/실패(status 로 구분)
    total        INTEGER NOT NULL,
    section      TEXT NOT NULL DEFAULT '',
    matched      TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL,
    detail       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_ranks_kw ON ranks(engine, area, keyword, collected_at);
CREATE INDEX IF NOT EXISTS idx_ranks_run ON ranks(run_id);
"""


class Store:
    def __init__(self, db_path: Path = config.DB_PATH):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path)
        self.db.executescript(_SCHEMA)

    def append(self, run_id: str, collected_at: str, date: str,
               records: list[RankRecord], kw_groups: dict[str, str]) -> int:
        rows = [(collected_at, date, run_id, r.engine, r.area, r.keyword,
                 kw_groups.get(r.keyword, "generic"), r.rank, r.total,
                 r.section, r.matched, r.status, r.detail)
                for r in records]
        with self.db:
            self.db.executemany(
                "INSERT INTO ranks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        return len(rows)

    def previous_run_map(self, before_run_id: str) -> dict[tuple[str, str, str], tuple]:
        """직전 런의 (engine, area, keyword) → (rank, status). 변화 알림 비교용."""
        cur = self.db.execute(
            "SELECT run_id FROM ranks WHERE run_id != ? "
            "ORDER BY collected_at DESC LIMIT 1", (before_run_id,))
        row = cur.fetchone()
        if not row:
            return {}
        prev = row[0]
        out: dict[tuple[str, str, str], tuple] = {}
        for eng, area, kw, rank, status in self.db.execute(
                "SELECT engine, area, keyword, rank, status FROM ranks "
                "WHERE run_id = ?", (prev,)):
            out[(eng, area, kw)] = (rank, status)
        return out

    def export_csv(self, path: Path, date: str | None = None) -> int:
        """전체(또는 특정 일자) 이력 CSV 내보내기 — 시트 없이도 공유 가능하도록."""
        q = "SELECT * FROM ranks"
        args: tuple = ()
        if date:
            q += " WHERE date = ?"
            args = (date,)
        cur = self.db.execute(q + " ORDER BY collected_at, engine, area, keyword", args)
        cols = [d[0] for d in cur.description]
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8-sig") as f:  # 엑셀 한글 호환 BOM
            w = csv.writer(f)
            w.writerow(cols)
            n = 0
            for row in cur:
                w.writerow(row)
                n += 1
        return n

    def close(self) -> None:
        self.db.close()


# ── Google Sheets 미러(선택) ─────────────────────────
SHEET_HEADERS = ["수집일시", "일자", "런ID", "엔진", "영역", "키워드", "그룹",
                 "순위", "결과수", "섹션", "매칭", "상태"]


def sheets_append(records: list[RankRecord], run_id: str, collected_at: str,
                  date: str, kw_groups: dict[str, str]) -> int:
    """KRM_SHEET_ID 설정 시에만 동작. gspread 미설치/실패는 호출부에서 로그만."""
    if not config.SHEET_ID:
        return 0
    import gspread  # 선택 의존 — Sheets 안 쓰는 환경에 강제하지 않는다
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_file(
        config.SERVICE_ACCOUNT, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    sh = gspread.authorize(creds).open_by_key(config.SHEET_ID)
    title = "키워드_순위"
    try:
        ws = sh.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows=4000, cols=len(SHEET_HEADERS))
        ws.append_row(SHEET_HEADERS, value_input_option="USER_ENTERED")
    rows = [[collected_at, date, run_id, r.engine, r.area, r.keyword,
             kw_groups.get(r.keyword, "generic"),
             r.rank if r.rank is not None else "", r.total, r.section,
             r.matched[:200], r.status] for r in records]
    ws.append_rows(rows, value_input_option="USER_ENTERED", table_range="A1")
    return len(rows)
