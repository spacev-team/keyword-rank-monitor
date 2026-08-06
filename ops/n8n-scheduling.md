# n8n 스케줄 → GitHub Actions 실행 가이드 (keyword-rank-monitor)

수집 아키텍처(EMM 과 동일): **n8n(스케줄) → GitHub `workflow_dispatch` API → Actions 러너(실행) → state 브랜치(SQLite 이력) + GitHub Pages(대시보드)**.
n8n 은 방아쇠만 당긴다 — 코드·시크릿·실행·상태·배포는 전부 GitHub 안에 있다.

## 스케줄 (KST)

| cron | mode | 내용 |
|---|---|---|
| `30 7 * * *` | `daily` | 전체 5개 엔진 — **구글 포함(하루 1회, 사용자 확정 2026-08-06)** |
| `30 11,16,21 * * *` | `core` | 네이버·다음·플레이스토어·앱스토어(구글 제외) |

→ 하루 4회 수집, 구글만 1회. 매 런 종료 시 대시보드 자동 재배포.

## 1. PAT 발급 (1회, 관리자)

GitHub → Settings → Developer settings → **Fine-grained personal access token**:
- Repository access: `spacev-team/keyword-rank-monitor` **만**
- Permissions → Repository permissions → **Actions: Read and write** (그 외 No access)
- 만료일을 n8n 워크플로 메모에 기록하고 만료 전 재발급

⚠️ EMM 의 `GitHub PAT (EMM)` 은 external-metrics-monitor 저장소 한정이라 **재사용 불가** —
기존 PAT 의 Repository access 에 이 repo 를 추가하거나 새 PAT 발급.

## 2. n8n 워크플로

- `n8n-workflow.json` 을 가져오기(Import)하거나, 이미 API 로 생성돼 있으면 활성화만.
- 자격증명 **GitHub PAT (KRM)** (Header Auth): Name=`Authorization`, Value=`Bearer <PAT>` —
  플레이스홀더 상태면 PAT 발급 후 실값으로 교체.
- HTTP 노드 성공 응답 = **204 No Content**. 404/401 이면 PAT 권한·만료 확인.

## 3. 안전장치 (repo 내장)

- **워치독**(`.github/workflows/watchdog.yml`): 매일 10:00 KST, 26시간 내 `collect daily`
  실행 이력이 없으면 대신 실행 + 경고.
- **동시 실행 직렬화**: `concurrency: krm-collect`.
- **수동 실행**: GitHub → Actions → Collect → Run workflow. `probe` = 네이버·다음 2키워드
  dry-run(저장 없음) — 러너 IP 봇차단 점검용.

## 4. 운영 절차

| 상황 | 절차 |
|---|---|
| 웹훅·SerpAPI 키 설정 | 로컬 `.env` 작성 → `gh secret set KRM_DOTENV -R spacev-team/keyword-rank-monitor < .env` |
| 수집 실패 | Actions 런 로그 확인 → 일시 오류면 Run workflow 재실행(이력은 run_id 단위 스냅샷 — 재실행해도 추세 왜곡 없음) |
| 이력 확인 | state 브랜치 `data/rank_history.sqlite` = 전체 이력. 로컬에서 `python run.py --export-csv out.csv` |
| 대시보드 | https://spacev-team.github.io/keyword-rank-monitor/ (매 런 자동 갱신) |

## 5. 알려진 리스크

- **구글**: 2025-01부터 검색에 JS 필수 → 직접 스크레이핑 불가. `KRM_DOTENV` 시크릿에
  `SERPAPI_KEY` 설정 시에만 실데이터 수집(미설정 시 `blocked` 기록 — 대시보드에 '차단/미설정' 표시).
- **러너 IP 봇차단(네이버·다음)**: `probe` 모드로 판정. 차단 시 폴백 = 로컬
  `scripts/register_tasks.ps1` 스케줄(이 경우 n8n 스케줄과 동시 가동 금지 — 이중 수집).
- **repo 공개**: org free 플랜에서 Pages 는 public repo 필수 → **키워드 목록·순위 데이터가
  공개됨**. 민감도가 올라가면 org 유료 전환(소스 private + Pages 공개) 또는 별도 호스팅 검토.
