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
| `37 3 * * *` | 12:37 (매일 1회) | `daily` | 네이버·다음·플레이·앱스토어 + **구글(월요일만, KRM_GOOGLE_DAYS)** |

`core`(구글 제외)는 스케줄에서 제거 — 수동 Run workflow 점검용으로만 남는다(하루 1회 통일, 2026-08-12).

- GitHub cron 은 5~20분 지연될 수 있다(추세 프록시 용도라 허용). 분값 37 = 혼잡 시간 회피.
- schedule 이벤트는 inputs 가 없으므로 스케줄 런은 항상 `daily` 로 실행된다.
- 동시 실행은 `concurrency: krm-collect` 로 직렬화.

## 운영 절차

| 상황 | 절차 |
|---|---|
| 수동 실행 | GitHub → Actions → Collect → Run workflow (mode 선택). `probe` = 네이버·다음 2키워드 dry-run(저장 없음) — 러너 IP 봇차단 점검용 |
| 시각 변경 | `collect.yml` 의 cron 수정 → push (UTC 기준임에 주의: KST−9h) |
| 웹훅·Serper 키 설정 | 로컬 `.env` 작성 → `gh secret set KRM_DOTENV -R spacev-team/keyword-rank-monitor < .env` |
| 수집 실패 | Actions 런 로그 확인 → Run workflow 재실행(런 = 독립 스냅샷이라 재실행 무해) |
| 이력 확인 | state 브랜치 `data/rank_history.sqlite`. 로컬: `python run.py --export-csv out.csv` |
| 대시보드 | https://spacev-team.github.io/keyword-rank-monitor/ (매 런 + docs 변경 push 시 자동 재배포) |

## 알려진 리스크

- **구글**: 2025-01부터 검색에 JS 필수 → 러너(데이터센터 IP)에선 직접 스크레이핑 불가
  (실제 브라우저여도 reCAPTCHA — 거주지 IP 로컬 수집안은 상시 가동 PC 필요로 기각,
  2026-08-07 사용자 확정: 100% 클라우드). 구글 공식 Custom Search JSON API 는 2026-01-20
  신규 고객 차단(403 실측 + 공식 문서) — 완전 무료 경로 소멸. 실데이터 경로:
  ① `KRM_DOTENV` 의 `SERPAPI_KEY`(고가) 또는
  ② `SERPER_KEY`(https://serper.dev — 가입 시 2,500쿼리 무료(카드 불필요), 이후 $50 선불
  50,000크레딧).
  둘 다 없으면 `blocked` 기록, 대시보드에 '차단/미설정' 표시. **구글 광고는 측정 제외**
  (유료 SerpAPI 전용 데이터 — 2026-08-07 사용자 확정. 수집기도 organic 만 기록,
  대시보드 구글 광고 카드 제거).
- **구글 크레딧 예산**(Serper 기준): 주 1회(월) × 브랜드 계열 69개 × 1크레딧(상위 10,
  `KRM_GOOGLE_NUM=10`) ≈ 월 300 → 가입 무료 2,500크레딧으로 **약 8개월**. 소진 시
  SerpAPI 무료 플랜(월 250 리셋, 카드 불필요)으로 `SERPAPI_KEY` 만 추가하면
  전환 — 이 경우 주 1회 × `KRM_GOOGLE_GROUPS=brand,adhoc`(30개) ≈ 월 129 로 영구 무료.
- **러너 IP 봇차단(네이버)**: 러너 IP 복불복로 간헐 차단(2026-08-06 실측). 수집기가 쿨다운
  재시도 후 조기 중단하고, 대시보드는 엔진별 '마지막 정상 런'을 보여주므로 자가 회복된다.
- **repo 공개**: org free 플랜에서 Pages 는 public repo 필수 → 키워드 목록·순위 데이터 공개.
- **cron 지연/드물게 스킵**: GitHub 부하에 따라 발생 가능. 하루 1회 수집이라 스킵되면
  그날 데이터가 비므로, 미실행 확인 시 Run workflow(daily)로 수동 보충. 지속 미실행 시
  Actions 탭에서 schedule 비활성화 여부 확인(60일 무커밋 시 GitHub 이 schedule 을
  자동 중지 — 커밋이 있으면 재활성).
