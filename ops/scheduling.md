# 수집 스케줄 운영 가이드 (keyword-rank-monitor)

아키텍처: **GitHub Actions cron(스케줄 내장) → 러너(수집) → state 브랜치(SQLite 이력) + GitHub Pages(대시보드)**.
토큰·n8n·외부 트리거가 전혀 없다 — 스케줄 변경 = `collect.yml`의 cron 수정 후 push.

> 이력: 원래 EMM 처럼 n8n → workflow_dispatch 구조로 구축했으나(2026-08-06),
> org fine-grained PAT 승인 대기 + 개인 토큰 과권한 문제로 cron 내장으로 전환(사용자 확정).
> 이 프로젝트는 런마다 독립 스냅샷(run_id)이라 트리거 중복이 무해해 EMM 의
> 'cron 금지(시트 중복 적재)' 원칙이 적용되지 않는다.

## 스케줄 (KST)

| cron (UTC) | KST | mode | 엔진 |
|---|---|---|---|
| `37 22 * * *` | 07:37 | `daily` | 네이버·**구글**·다음·플레이·앱스토어 (구글은 하루 1회) |
| `37 2,7,12 * * *` | 11:37 / 16:37 / 21:37 | `core` | 구글 제외 4개 |

- GitHub cron 은 5~20분 지연될 수 있다(추세 프록시 용도라 허용). 분값 37 = 혼잡 시간 회피.
- schedule 이벤트는 inputs 가 없으므로 러너가 UTC 시각으로 모드를 결정한다(22시대 = daily).
- 동시 실행은 `concurrency: krm-collect` 로 직렬화.

## 운영 절차

| 상황 | 절차 |
|---|---|
| 수동 실행 | GitHub → Actions → Collect → Run workflow (mode 선택). `probe` = 네이버·다음 2키워드 dry-run(저장 없음) — 러너 IP 봇차단 점검용 |
| 시각 변경 | `collect.yml` 의 cron 수정 → push (UTC 기준임에 주의: KST−9h) |
| 웹훅·SerpAPI 키 설정 | 로컬 `.env` 작성 → `gh secret set KRM_DOTENV -R spacev-team/keyword-rank-monitor < .env` |
| 수집 실패 | Actions 런 로그 확인 → Run workflow 재실행(런 = 독립 스냅샷이라 재실행 무해) |
| 이력 확인 | state 브랜치 `data/rank_history.sqlite`. 로컬: `python run.py --export-csv out.csv` |
| 대시보드 | https://spacev-team.github.io/keyword-rank-monitor/ (매 런 + docs 변경 push 시 자동 재배포) |

## 알려진 리스크

- **구글**: 2025-01부터 검색에 JS 필수 → 직접 스크레이핑 불가. `KRM_DOTENV` 시크릿에
  `SERPAPI_KEY` 설정 시에만 실데이터(미설정 시 `blocked` 기록, 대시보드에 '차단/미설정' 표시).
- **러너 IP 봇차단(네이버)**: 러너 IP 복불복로 간헐 차단(2026-08-06 실측). 수집기가 쿨다운
  재시도 후 조기 중단하고, 대시보드는 엔진별 '마지막 정상 런'을 보여주므로 자가 회복된다.
- **repo 공개**: org free 플랜에서 Pages 는 public repo 필수 → 키워드 목록·순위 데이터 공개.
- **cron 지연/드물게 스킵**: GitHub 부하에 따라 발생 가능. 하루 4회 중 1회 빠져도 추세엔
  영향 미미. 지속 미실행 시 Actions 탭에서 schedule 비활성화 여부 확인(60일 무커밋 시
  GitHub 이 schedule 을 자동 중지 — 커밋이 있으면 재활성).
