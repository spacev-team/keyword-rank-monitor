# keyword-rank-monitor — 삼삼엠투 키워드 순위 모니터링

브랜드 키워드(30 + 확장 39) + 검색광고 매출 상위 일반 키워드(171) = **240개 키워드**가
**네이버 · 구글 · 다음 · Play Store · App Store** 에서 몇 위에 노출되는지 매일 4회 수집한다.
네이버/구글/다음은 **검색광고(ad)와 오가닉(organic)을 분리 측정**한다.

**대시보드**: https://spacev-team.github.io/keyword-rank-monitor/ (매 수집 런 종료 시 자동 갱신)

## 아키텍처

```
GitHub Actions cron(스케줄 내장) → 러너(수집)
                                   ├─ state 브랜치: data/rank_history.sqlite (전체 이력)
                                   └─ GitHub Pages: docs/ 대시보드 + data/*.json
```

토큰·외부 스케줄러 없이 repo 단독으로 동작한다(cron 채택 경위는 [`ops/scheduling.md`](ops/scheduling.md)).

| 스케줄(KST) | mode | 엔진 |
|---|---|---|
| 07:37 | `daily` | 네이버·**구글**·다음·플레이·앱스토어 (구글은 하루 1회만) |
| 11:37 / 16:37 / 21:37 | `core` | 구글 제외 4개 엔진 |

GitHub cron 특성상 5~20분 지연될 수 있다. 운영 절차는 [`ops/scheduling.md`](ops/scheduling.md).

## 측정 정의

| 엔진 | 영역 | 방법 | rank 의 의미 |
|---|---|---|---|
| naver | ad | PC 통합검색 HTML 파싱 | 파워링크·브랜드검색 광고 유닛(상단+하단 통합) 중 자사 순번 |
| naver | organic | 〃 | 광고 제외 결과 블록 DOM 순서 중 자사 첫 매칭(섹션명 기록) |
| google | organic | SerpAPI(고가) → Serper.dev(저가) → 직접 파싱 순 | 상동. **광고는 측정 제외**(유료 SerpAPI 없이 측정 불가 — 2026-08-07 확정) |
| daum | ad / organic | 통합검색 HTML 파싱 | 프리미엄링크 / 오가닉 블록 |
| playstore | app | google_play_scraper.search (상위 30) | 검색 결과 중 자사 앱 순번 |
| appstore | app | iTunes Search API (상위 200) | 상동 |

- 자사 판별: 결과 URL 이 `33m2.co.kr`(서브도메인·광고 리다이렉트 포함) 또는 자사 앱 상세 링크.
- `rank=NULL` 은 `status` 로 구분: `not_found`(정상 파싱, 미노출) / `no_section`(광고 영역
  없음) / `blocked`(봇 차단) / `parse_fail`(SERP 구조 변경 의심) / `error`(네트워크 등).
  → **미노출과 수집 실패를 절대 섞지 않는다.**
- ⚠️ 구글은 2025-01부터 검색에 JS 필수 → 데이터센터 IP 직접 스크레이핑 불가. 구글 공식
  Custom Search JSON API 도 2026-01-20부터 신규 고객 차단(무료 경로 소멸). 실데이터 경로는
  ① `SERPAPI_KEY`(고가) 또는 ② `SERPER_KEY`(Serper.dev — 가입 시 2,500쿼리
  무료, 이후 선불 크레딧). 둘 다 없으면 `blocked` 기록. 구글 광고 순위는 유료
  SerpAPI 전용 데이터라 **프로젝트에서 측정 제외**(수집기도 organic 만 기록).
- ⚠️ Serper 경로 제약: 크레딧 절약을 위해 구글만 브랜드 계열 키워드(`KRM_GOOGLE_GROUPS`,
  기본 brand+brand_ext ≈ 69개 — 일반 171개는 네이버·다음에서만 측정) × **주 1회(월요일,
  `KRM_GOOGLE_DAYS`)** 수집. 결과 수는 상위 10
  (`KRM_GOOGLE_NUM`, 10 초과 시 크레딧 2배). 무료 2,500크레딧 ≈ 8개월, 소진 시
  SerpAPI 무료 플랜(월 250 리셋)으로 전환 가능.
- ⚠️ SERP 는 기기·지역·개인화로 달라짐 — 동일 방법론 반복 측정의 **추세 추적 프록시**.

## 로컬 실행 (개발·점검용)

```
pip install -r requirements.txt
python run.py --mode daily                  # 구글 포함 전체
python run.py --mode core                   # 구글 제외
python run.py --keywords 삼삼엠투,단기임대 --dry-run   # 스모크 테스트
python run.py --export-csv out.csv          # 이력 CSV(엑셀 호환 BOM)
python export_dashboard.py                  # docs/data/*.json 재생성
```

수집은 Actions cron 이 전담한다 — 로컬 실행은 점검·개발용(로컬 SQLite 에만 기록되고 운영 이력과 섞이지 않음).

## 저장

- **SQLite** `data/rank_history.sqlite` — 진실 원본, Actions `state` 브랜치에 보존(매 런 복원→적재→push).
- **docs/data/latest.json · trends.json** — 대시보드용, 매 런 `export_dashboard.py` 가 생성.
- **Google Sheets 미러(선택)** — `KRM_SHEET_ID` + 서비스계정 설정 시 `키워드_순위` 시트 누적.

## 알림(선택)

`KRM_GOOGLE_CHAT_WEBHOOK_URL` 설정 시 런마다 요약(커버리지·TOP3·수집이상) +
직전 런 대비 악화 알림(노출 이탈 / 5계단 이상 하락 / 브랜드 TOP3 이탈).

## 키워드 관리

`keywords.py` — 브랜드 확정 목록 / 확장(어간×수식어 자동 생성) / 일반 목록.
수정 후 push 만 하면 다음 런부터 반영된다.
