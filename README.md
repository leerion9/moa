# moa (모아) 자동매매 시스템

한국 주식(KOSPI/KOSDAQ) 대상 52주 신고가 돌파 매매 자동화 프로젝트입니다.  
증권사: **한국투자증권 (KIS OpenAPI)**

---

## 대화 규칙 (중요)
- 이 저장소 작업 중 AI 응답은 **반드시 한국어**로만 진행합니다.
- 동일 요청을 반복하지 않도록, 합의된 요구사항/규칙은 README/.cursorrules에 명시된 내용을 우선합니다.

---

## 매매 전략 개요

주식 매매는 3단계로 정의합니다: **종목 선정 → 매수 → 매도**

### 1단계: 종목 선정 (매수 후보 감시 목록)

**(1차 필터)**
| 조건 | 기준 |
|------|------|
| 시가총액 | 800억 이하 제외 |
| ETF/우선주 | 제외 (`INCLUDE_ETF`, `INCLUDE_PREFERRED` 설정으로 제어) |
| 상대강도 필터 | 전종목 6개월 주가수익률 상위 10% 종목만 포함 (직접 계산) |

**(2차 필터 - 전략 모드별)**
| 모드 | 조건 | 개념 |
|------|------|------|
| 전략1 (`STRATEGY_MODE=1`) | 최근 60일 이내 52주 신고가 없을 것 | 최초 돌파 |
| 전략2 (`STRATEGY_MODE=2`) | 최근 10일 내 52주 신고가 5회 이상 | 추세 지속 |

### 2단계: 매수 조건
- 후보 종목이 **52주 신고가 가격을 터치**하는 시점에 해당 가격으로 호가 매수 주문
- 거래량 동반 필수: **당일 누적 거래량 ≥ 20일 평균 거래량**

### 3단계: 매도 조건
- **트레일링 스탑**: 고점 대비 **-7.5% 하락** 시 전량 시장가 매도
- 손절/익절 구분 없이 단일 트레일링 조건으로 처리

### 포지션 관리
- 종목당 가용 현금의 **25%**, 최대 **4종목** 동시 보유
- 신용/마진 사용 금지 (현금 거래만)

---

## 디렉토리 구조
```text
moa/
├── config/
│   ├── settings.py
│   └── korea_market_holidays.txt
├── core/
│   ├── api_client.py         # KIS API 클라이언트
│   ├── logger.py             # 로거 (system.log, trades.csv, signals.csv)
│   ├── order.py              # 주문 실행 (매수/매도)
│   ├── strategy.py           # 52주 신고가 전략 로직
│   ├── trading_day.py        # 휴장일 판단
│   ├── universe_builder.py   # 유니버스 빌더 (1차/RS/2차 필터 파이프라인)
│   ├── universe_cache.py     # 당일 유니버스 캐시
│   ├── history_cache.py      # Naver 일봉 영구 캐시 (증분 갱신)
│   ├── universe_xlsx.py      # universe.xlsx writer (n+15일 가격 추적)
│   ├── result_xlsx.py        # result.xlsx writer (n+15일 가격 추적)
│   ├── xlsx_price_track.py   # n+1~n+15 고가 대비 % 갱신
│   ├── open_positions.py     # 기존 보유 종목 조회 (매수 감시 제외)
│   ├── kis_token_cache.py    # KIS 토큰 파일 캐시 (main/vi_collector 공유)
│   ├── vi_collector_logic.py # VI 이벤트·분봉 판정 로직
│   ├── vi_universe_xlsx.py   # vi_universe.xlsx writer
│   ├── gap_collector_logic.py# 갭상승 회복 전략 시뮬 로직
│   ├── gap_result_xlsx.py    # gap_result/gap_backfill xlsx writer
│   ├── gap_naver_ticks.py    # 네이버 체결(sise_time) 크롤·분봉 변환
│   ├── gap_backfill_queue.py # 과거 소급 후보 큐 관리
│   ├── naver_universe.py     # 네이버 종목 스크래핑
│   ├── naver_symbol_master.py
│   ├── result_csv.py
│   └── symbol_resolver.py
├── scripts/
│   ├── build_result.py
│   ├── build_universe.py     # 유니버스 배치 빌드 CLI
│   ├── vi_collector.py       # 장마감 VI 유니버스 수집
│   ├── gap_collector.py      # 장마감 갭상승 회복 전략 일일 수집
│   ├── gap_backfill.py       # 갭 전략 과거 소급 백필 (네이버 fchart 분봉)
│   ├── rebuild_gap_backfill_xlsx.py  # 분봉 캐시로 gap_backfill.xlsx 재생성
│   ├── update_holidays.py
│   └── update_symbol_master.py
├── data/
│   ├── history_cache/        # 일봉 영구 캐시 (git 제외)
│   ├── gap_backfill/         # 소급 큐·체결 캐시 (git 제외)
│   ├── kis_token_cache.json  # 토큰 공유 (git 제외)
│   └── logs/{live|paper}/    # system.log, xlsx, csv
├── tests/                    # 144개 테스트
├── .cursorrules
├── .env.example
├── main.py
└── requirements.txt
```

---

## 설치
```bash
pip install -r requirements.txt
```

## 환경변수
`.env.example`을 복사해 `.env`를 만들고 값 입력:
```bash
APP_KEY=...
APP_SECRET=...
ACCOUNT_NO=12345678-01
IS_PAPER_TRADING=true
SIM_MODE=true           # 현재: live API + 가상주문. 실매매 전환 시 false
STRATEGY_MODE=1         # 1=최초돌파, 2=추세지속
TRAILING_STOP_PCT=0.075
W52_MAX_GAP_PCT=0.30    # 52주 신고가 대비 전일 종가 gap 30% 이상 낮으면 유니버스 제외
RESULT_WRITE_HHMM=15:32 # 장 종료 후 result.xlsx 기록
SHUTDOWN_HHMM=15:40     # 봇 종료
# 갭상승 회복 전략 (gap_collector / gap_backfill)
GAP_MIN_PCT=3.0
GAP_MAX_PCT=9.0
GAP_DIP_MIN_PCT=3.0
GAP_TRAILING_STOP_PCT=0.05
GAP_BUY_QTY=1
GAP_BACKFILL_BATCH_SIZE=30
GAP_NAVER_TICK_DELAY_SEC=0.15
```

## 실행

### 52주 신고가 매매 (main.py)
```bash
# 최초 1회 — 1차 필터 종목 history 풀 수집 (~1시간, 2026-05-26 완료: 1714종)
python -m scripts.build_universe --bootstrap-history

# 매매 봇 (운영: PC 08:20 기동, main.py 08:30 → 증분 갱신 ~12-17분 → 09:00 감시)
python main.py

# 수동 dual 유니버스만 빌드 (선택)
python -m scripts.build_universe
```

### 장마감 후 별도 프로세스 (main.py와 분리)
```bash
# VI 유니버스 수집 → data/logs/{mode}/vi_universe.xlsx
python -m scripts.vi_collector

# 갭상승 회복 전략 일일 수집 -> gap_result.xlsx (+ 당일 gap_backfill 자동)
python -m scripts.gap_collector
# python -m scripts.gap_collector --skip-backfill  # Naver 백필만 생략
```

### 갭 전략 과거 소급 (gap_backfill)
```bash
# 1) history_cache 일봉으로 연도별 후보 큐 생성 (HTTP 없음)
python -m scripts.gap_backfill plan --year 2025

# 2) 처리 예정 확인 (크롤링 없음, dry-run)
python -m scripts.gap_backfill run --year 2025 --limit 30

# 3) 실제 네이버 체결 크롤링 + 시뮬 + gap_backfill.xlsx 기록
python -m scripts.gap_backfill run --year 2025 --limit 30 --execute

# 진행 상태
python -m scripts.gap_backfill status --year 2025

# 분봉 캐시만으로 xlsx 재생성 (HTTP 없음, 15:30 규칙 반영 후)
python -m scripts.rebuild_gap_backfill_xlsx --year 2026 --from 20260529 --to 20260609
```

### gap_backfill 분봉 보관 (2026-05-29~)

**정책**: 사용자가 중단 요청할 때까지 **갭 후보 종목의 분봉을 모두 로컬에 영구 보관**한다.  
`gap_backfill run --execute` 시 네이버 fchart 분봉을 크롤링하면 자동 저장된다.

| 항목 | 경로 |
|------|------|
| **분봉 캐시** | `data/gap_backfill/ticks/{YYYYMMDD}/{종목코드}_minute.json` |
| 백필 큐 | `data/gap_backfill/queue_{YYYY}.json` |
| 진행 상태 | `data/gap_backfill/state.json` |
| 시뮬 결과 | `data/logs/{live\|paper}/gap_backfill.xlsx` |

- git **미포함** (`.gitignore`: `data/gap_backfill/`). PC 백업은 `ticks/` 폴더를 별도 복사.
- xlsx만으로는 분봉 원본 복구 불가 → **ticks 보관 필수**.
- 시뮬 규칙: **09:00~15:30 정규장**만 사용. 종가 매도 = **15:30 시각** + **일봉 정규장 종가**(장후 가격 아님).
- xlsx **종가매도여부**: 트레일링=0, 15:30 종가 매도=1
- xlsx **종가매도수익률**: 행별 가정 수익률 (매수가→당일 종가 매도). 종가매도여부=1이면 수익률과 동일
- xlsx **익일시가매도수익률**: 행별 가정 수익률 (매수가→익거래일 시가 매도). 매수 당일엔 익일 시가 없어 **빈칸** → 다음 `gap_collector` 실행 시 `history_cache`로 **자동 보정**

## 테스트
```bash
python -m pytest -q
```

---

## 개발 진행 현황

### ✅ Phase 1 — 완료 (커밋 `b56438b`)
| 파일 | 내용 |
|------|------|
| `config/settings.py` | 전체 환경변수 설정 |
| `core/api_client.py` | KIS API 클라이언트 (시세·주문·체결 조회) |
| `core/strategy.py` | W52HighStrategy (매수신호 + 트레일링스탑) |
| `core/order.py` | 주문 실행 (지정가매수·시장가매도) |
| `core/logger.py` | 로거 (system.log, trades.csv, signals.csv) |
| `core/trading_day.py` | 휴장일 판단 |
| `core/universe_cache.py` | 유니버스 캐시 로드/저장 |
| `core/naver_universe.py` | 네이버 시총 스크래핑 |
| `core/naver_symbol_master.py` | 종목 마스터 |
| `core/result_csv.py` | 일별 결과 CSV |
| `main.py` | MoaRunner 전체 루프 |

### ✅ Phase 2 — 완료 (커밋 `24dc660` ~ `73a1452`)

#### Phase 2-1: KIS API 확장 (`core/api_client.py`)
- `Quote` 데이터클래스에 `w52_high`, `w52_low` 필드 추가 (기존 코드 호환)
- `SymbolHistory` 데이터클래스 신규 추가
- `get_symbol_history(symbol, days=135)` — FHKST03010100으로 52주 고저가 + N일 OHLCV 단일 호출, 자동 페이징
- `get_market_cap_list()` — KOSPI+KOSDAQ 시총 내림차순 리스트
- `_safe_abs_int()` 헬퍼 추가

#### Phase 2-2: 유니버스 빌더 (`core/universe_builder.py`)
- 순수 함수 (독립 테스트 가능):
  - `is_preferred_stock(symbol)` — 우선주 판별 (코드 끝자리 != 0)
  - `is_etf_by_name(name)` — ETF 종목명 기반 판별
  - `calc_vol_ma(bars, days=20)` — 평균 거래량
  - `calc_w52_hit_count(bars, w52_high, lookback)` — 신고가 터치 횟수
  - `calc_rs_return(bars, lookback=126)` — 6개월 수익률
  - `compute_rs_top_pct(returns, top_pct)` — RS 상위 N% 필터
  - `build_features(history, fresh_days, cont_days)` — CachedSymbol 피처 계산
  - `apply_second_filter(features, strategy_mode, ...)` — 2차 필터
- `UniverseBuilder` 클래스: 6단계 파이프라인
  1. 시총 목록 수집 (**Naver primary → KIS fallback**)
  2. 1차 필터 (시총·ETF·우선주)
  3. 종목별 히스토리 수집 (**Naver primary** 30페이지 × 10봉 ≈ 300봉/종목 → KIS fallback)
  4. RS 필터 (6개월 수익률 상위 10%)
  5. 피처 계산 (w52_high, vol_ma20, w52_hit_60d, w52_hit_10d)
  6. 2차 필터 (전략 모드별)
- `naver_universe.py`에 `fetch_market_cap_list()` 및 `fetch_symbol_history_naver()` 추가

#### Phase 2-3: main.py 연결
- `prepare_universe()`에서 캐시 없을 때 `UniverseBuilder.build()` 자동 호출
- 빌드 성공 시 `save_cache()` 저장, 실패 시 에러 로그 후 빈 목록으로 계속

#### Phase 2-4: 배치 스크립트 (`scripts/build_universe.py`)
- CLI: `python -m scripts.build_universe [--date YYYYMMDD] [--strategy 1|2] [--force]`
- 캐시 존재 확인 → 종목 마스터 로드 → 빌드 → 저장 → 결과 출력

**테스트 현황**: 총 **70개 통과** (api_client 13, universe_builder 38, 기타 19)

### ✅ Phase 3 — 완료

#### 3-1. ETF 필터 보완 ✅
- `KISApiClient.get_market_cap_list()`가 응답에서 `hts_kor_isnm`(종목명)을 추출해 `_last_cap_list_names`에 저장
- `naver_universe.fetch_market_cap_list()`도 `(cap_list, names_dict)` 반환으로 변경
- `UniverseBuilder`가 `symbol_names`(마스터) 없을 때 `_cap_list_names`(시총 API 부산물)를 ETF 이름 필터 fallback으로 사용
- 신규 테스트: `test_builder_etf_excluded_by_cap_list_names_fallback`

#### 3-2. `korea_market_holidays.txt` 연도별 자동 관리 ✅
- `scripts/update_holidays.py` 추가: KIS `CTCA0903R` 월별 호출 → 평일 휴장일(opnd_yn=N) 자동 수집
- `update_holiday_file()` 함수: force 모드(해당 연도 재작성) / append 모드(중복 없이 추가)
- 신규 테스트: `test_trading_day.py`에 6개 추가
- 사용: `python -m scripts.update_holidays --year 2027`

#### 3-3. 페이퍼 트레이딩 end-to-end 검증
- `.env` 설정(모의투자 계정) 후 `build_universe.py` → `main.py` 실제 실행 (사용자가 직접 진행)
- 로그 파일 및 trades.csv 정상 기록 확인

#### 3-4. `on_quote()` 거래량 skip 로직 개선 ✅
- `SymbolState.skip` 필드 제거
- 거래량 미충족 시 해당 tick만 패스 — 다음 tick에서 재확인 (오전 부족 → 오후 충족 케이스 정상 처리)
- `watchlist_symbols()`에서 `skip` 조건 제거, `bought` 여부만 확인
- 신규 테스트: `test_volume_insufficient_can_retry_next_tick`, `test_watchlist_symbols_excludes_only_bought`

#### 3-5. 결과 분석 도구 검토 ✅
- `result_csv.py` 내 `append_result1_rows` 함수(line 666~) 존재 확인 — import 정상

**테스트 현황**: 총 **80개 통과** (api_client 13, universe_builder 39, strategy 8, trading_day 12, result_fifo 6, 기타 2)

### ✅ Phase 5 — 완료 (커밋 `e6d8766` ~ `efca999`)

#### 5-1. n+15일 가격 추적 ✅
- `universe.xlsx` / `result.xlsx`에 n(기준가)과 n+1~n+15 고가 대비 % 컬럼 추가
- 장 마감 후 `history_cache`로 일별 갱신 (`core/xlsx_price_track.py`)

#### 5-2. VI 유니버스 배치 수집기 ✅
- `scripts/vi_collector.py` — 정적 상승 VI 1차 이벤트를 KIS API·분봉으로 수집
- 결과: `data/logs/{mode}/vi_universe.xlsx` (main.py와 별도 프로세스)
- 2차상승vi 확정, 발동vs해제·거래대금·시총 컬럼 보강

#### 5-3. KIS 토큰 파일 캐시 ✅
- `data/kis_token_cache.json` — main.py 발급 토큰을 vi_collector가 재사용

#### 5-4. 52주 유니버스 gap 필터 ✅
- `W52_MAX_GAP_PCT=0.30` — 전일 종가가 52주 신고가 대비 30% 이상 낮은 종목 제외

#### 5-5. 갭상승 회복 전략 (검증용, main과 별도) ✅
- **전략**: 전일 대비 시가 +3~+9% 갭 → 시가 대비 -3% 하락 후 → 시가 회복 시 매수 → 트레일링 -5% 또는 종가 매도
- **일일 수집**: `gap_collector` — KIS 분봉 기반 당일 시뮬 → `gap_result.xlsx`
- **과거 소급**: `gap_backfill` — 네이버 **fchart 분봉** (`--execute` 시 HTTP) → `gap_backfill.xlsx` (폴백: `sise_time`)
- 체결·분봉 캐시: `data/gap_backfill/ticks/{YYYYMMDD}/{종목}_minute.json`, 큐: `queue_YYYY.json`
- **xlsx 공통 컬럼 (32개)**: … + **종가매도수익률**·**익일시가매도수익률**·**종가매도여부**

**테스트 현황**: 총 **144개 통과**

### 📋 Phase 6 — 갭 전략 운영·백필 (2026-06-09)

#### 6-1. gap_collector 일일 수집 ✅ (운영)
- `gap_collector` 장마감(15:35~) 실행 **정상** 확인 중 (며칠 더 모니터링)
- 결과: `data/logs/live/gap_result.xlsx` — **KIS 당일 분봉** (`FHKST03010200`)
- **같은 실행**에서 당일 `gap_backfill.xlsx`(Naver fchart) + `ticks/` 분봉 캐시도 자동 갱신 (`--skip-backfill`로 생략 가능)
- **익일시가매도수익률**: 매수일 다음 거래일 시가가 `history_cache`에 생기면 빈칸 자동 보정 (전일 행)
- **비교·백테스트 기준은 gap_collector(KIS)가 정답**

#### 6-2. gap_backfill 2026-01-01~06-05 크롤링 ✅ (데이터만, xlsx 재정리됨)
- `--from 20260101 --to 20260605 --execute` 로 후보 13,724건 처리 완료
- 네이버 **sise_time**은 과거 일자 **0건** → **fchart 분봉** API로 전환 (`core/gap_naver_ticks.py`)
- **1~5월**: 네이버 fchart 보관 기간 밖 → 분봉 없음 → xlsx 0건
- **5/29~6/5** (6/3 휴장 제외): 분봉 캐시 있는 날만 기록

#### 6-3. KIS vs Naver 백필 비교 (2026-06-09)

| 항목 | gap_collector (KIS) | gap_backfill (Naver fchart) |
|------|---------------------|----------------------------|
| 6/9 기록 종목 | **79** | **82** (수정 전·후 논의) |
| 6/9 수익률 합 | **-165%** | **+106%** (15:30 수정 전) |
| 분봉 | 고/저/종 | **종가 1개** (high=low=price) |
| 시간 | 09:00~15:30 | 08:30~15:58 (원본) |

**차이 원인**: Naver fchart는 분당 종가만 있어 트레일링 -5%·매수 시가 회복 판정이 KIS와 다름. **과거 소급 백필은 KIS와 1:1 비교 부적합**.

#### 6-4. 네이버 과거 분봉 한계 (합의)
- **fchart 분봉**: 최근 **약 5~7거래일** 롤링 보관. 1주일 넘은 과거 **무료 소급 불가**
- **sise_time**(시간별시세): 과거 날짜 지정해도 거의 0건
- **KIS**: 당일 분봉만. **앞으로 매일 gap_collector로 쌓는 것**이 무료 유일 방안
- 유료 데이터 없이 **1주일+ 과거 틱/분봉**은 현실적으로 불가

#### 6-5. 정규장(09:00~15:30) 시뮬 수정 ✅

**문제**: Naver fchart에 **장전(~08:30)**·**장후(~15:57)** 봉 포함 → 장전 가격으로 dip·매수 판정 왜곡.

**수정** (`core/gap_collector_logic.py`, `core/gap_naver_ticks.py`, `scripts/gap_backfill.py`):
- **09:00~15:30** 정규장만 dip·매수·트레일링 (`filter_regular_session_bars`, `in_regular_session`)
- **종가 매도**: `sell_hhmmss=153000`, 가격=**일봉 종가**

**xlsx 재생성** (`scripts/rebuild_gap_backfill_xlsx.py`):
- 로컬 분봉 캐시만 사용, HTTP 없음, `--from`/`--to` 날짜 필터
- `data/logs/live/gap_backfill.xlsx` **2026 시트** (2026-06-09 재빌드)

| 날짜 | 건수 |
|------|------|
| 2026-05-29 | 37 |
| 2026-06-01 | 20 |
| 2026-06-02 | 15 |
| 2026-06-04 | 14 |
| 2026-06-05 | 11 |
| 2026-06-08 | 3 |
| 2026-06-09 | **65** |
| **합계** | **165** |

- 장전 매수·15:30 이후 매도: **0건**

#### 6-6. 익일시가매도수익률 자동 보정 ✅
- 매수 당일 xlsx 기록 시 익일 시가가 없어 **빈칸** → 다음 `gap_collector` 실행 시 `history_cache`로 **전일 행 자동 채움**
- `gap_result.xlsx` + `gap_backfill.xlsx` 동시 갱신 (`refresh_gap_xlsx_next_open_returns`)

#### 6-7. 다음에 할 일 (미정)
- gap_collector vs gap_backfill **6/9 상세 비교표** (요청 시)
- 과거 백테스트: **일봉 근사** 별도 검토 또는 **당일 KIS 분봉 누적 저장** 설계

### 📋 Phase 4 — 실전 운영 이슈 정리 (2026-05-26 live 실행 분석)

> **목적**: 5/26 실매매 시간대 실행 결과를 기록하고, 확인된 문제를 수정 전에 문서화한다.
> **다음 단계**: 아래 이슈를 우선순위대로 하나씩 수정.

#### 5/26 실행 요약

| 항목 | 내용 |
|------|------|
| 실행 | `main.py`, `mode=live`, `STRATEGY_MODE=1` |
| 유니버스 빌드 | 08:40 ~ 09:44 (약 64분, `main.py` 내 인라인 빌드) |
| 감시 종목 | **19종목** (전략1) |
| 매수 | **1건** — 380540(옵티코어) @ 5,550원, 1주, 09:44:32 |
| 매도 | 없음 (트레일링 스탑 미발동) |
| 종료 | 15:35 로그 후 중단 (15:40 정상 shutdown 미완료) |
| result.xlsx | **미생성** (`data/logs/live/result.xlsx` 없음) |

#### 5/26 유니버스 빌드 실측 (전략1)

| 단계 | 조건 | 결과 |
|------|------|------|
| Step1 | Naver 시총 목록 | 3,956종목 |
| Step2 | 시총 800억↓ 제외 | 1,814 제외 → **1,721종목** |
| Step2 | 우선주 제외 | 74 제외 |
| Step2 | ETF 제외 | 347 제외 |
| Step3 | Naver 히스토리 (1,721종목 × 30페이지) | **약 64분** |
| Step4 | RS 6개월 수익률 상위 10% | 171종목 |
| Step5 | 피처 계산 (w52_high>0, 봉≥20) | 171종목 |
| Step6 | 전략1 2차 (60일 내 신고가 0회) | **19종목** |

**전략2 유니버스**: 당일 생성·감시 없음 (현재 코드는 `STRATEGY_MODE` 하나만 운영).

#### 5/26 매매 기록 위치

| 파일 | 내용 |
|------|------|
| `data/logs/live/system.log` | `[가상매수] 380540` (09:44:32) |
| `data/logs/live/trades.csv` | BUY 1주, `order_id=SIM` |
| `data/logs/live/signals.csv` | `action=SIM_BUY` |
| `data/logs/live/result.xlsx` | 없음 (15:40 전 프로세스 종료) |
| `data/logs/paper/result.xlsx` | 5/21자 (오늘 실행과 무관) |

---

#### 확인된 이슈 (5건)

**이슈 1 — 유니버스 빌드가 장중에 너무 오래 걸림 (~64분)** ✅ (2026-05-26 수정)

- **원인**: 1차 필터 통과 종목 전체 30페이지 스크래핑 후 RS 필터.
- **수정**: `data/history_cache/` 영구 캐시 + 매일 page1 증분 merge. 최초 bootstrap 1714종 (~67분) 완료.
- **일일 소요**: 증분 갱신 + dual 유니버스 **약 12~17분** → `main.py` **08:30** 가동 권장 (PC **08:20**).
- **파일**: `core/history_cache.py`, `scripts/build_universe.py --bootstrap-history`

**이슈 2 — 전략1·전략2 유니버스 동시 운영 미구현** ✅ (2026-05-26 수정)

- **수정**: `build_dual()` — `universe_cache_YYYYMMDD_s1.json` / `_s2.json` 각각 저장.
- **main.py**: 전략1+2 동시 감시, 포지션 **합산 4종** 공유, 종목별 `strategy_mode` 태그.
- **기록**: `data/logs/{mode}/universe.xlsx` — 날짜·전략·RS·52주고가 등 append.
- **파일**: `core/universe_builder.py`, `core/universe_xlsx.py`, `main.py`

**이슈 3 — result.xlsx 미생성 (15:40 전 프로세스 종료)** ✅ (2026-05-26 수정)

- **원인**: `result.xlsx`가 `SHUTDOWN_HHMM`(15:40)과 묶여 있었음. PC 자동 종료(15:40)와 겹치면 저장 전 종료.
- **수정**: `RESULT_WRITE_HHMM=15:32` — 장 종료(15:30) 2분 후 result 기록, `SHUTDOWN_HHMM`(15:40)은 봇 종료만 담당.
- **파일**: `config/settings.py`, `main.py`, `.env.example`

**이슈 4 — `mode=live`와 `SIM_MODE` 혼동** (운영 메모만, 코드 작업 보류)

- **두 설정은 별개**:
  - `IS_PAPER_TRADING=false` → `mode=live` (KIS **실계좌 API**로 시세·잔고 조회)
  - `SIM_MODE=true` (기본값) → **주문 API 미호출**, 1주 체결 **가정** 후 로그/CSV만 기록 (`order_id=SIM`, `[가상매수]`)
- **현재 운영**: 실계좌 API + SIM 주문 (시세는 live, 주문은 가상 시뮬).
- **실매매 전환 시 변경**:
  1. 먼저 `IS_PAPER_TRADING=true`, `SIM_MODE=false`로 모의투자 실주문 검증
  2. 확인 후 `IS_PAPER_TRADING=false`, `SIM_MODE=false`로 실계좌 실주문

**이슈 6 — 장 시작 전 유니버스 준비** ✅ (운영)

- **운영**: PC **08:20** 기동 → `main.py` **08:30** (증분 ~12-17분) → **09:00** 감시.
- `main.py`가 history 증분 + dual 유니버스 + `universe.xlsx`까지 자동 처리.
- bootstrap 미완료 시 최초 1회: `python -m scripts.build_universe --bootstrap-history`

**이슈 5 — `vol_ma20=0` 종목 거래량 조건 무력화** ✅ (2026-05-26 수정)

- **코드**: `current_volume < vol_ma20` → `vol_ma20=0`이면 거래량 조건 **항상 통과**.
- **5/26**: 380540 캐시 `vol_ma20=0` → 거래량 필터 우회 후 가상매수.
- **수정**: `MIN_VOL_MA20=1` — `build_features()` 제외, `load_cache()` 구 캐시 필터, `on_quote()` 이중 차단.
- **파일**: `core/universe_builder.py`, `core/strategy.py`, 테스트 2건 추가.

---

#### 수정 예정 목록 (우선순위)

| # | 항목 | 상태 |
|---|------|------|
| 1 | 유니버스 빌드 속도 (history_cache 증분) | ✅ 완료 |
| 2 | 전략1·2 dual 유니버스·감시 | ✅ 완료 |
| 3 | result.xlsx 저장 시점 (15:32 분리) | ✅ 완료 |
| 4 | SIM_MODE / live 모드 — 운영 메모 | 📝 메모만 |
| 5 | vol_ma20=0 종목 필터 추가 | ✅ 완료 |
| 6 | 장 시작 전 main 08:30 가동 | ✅ 운영 |
| 7 | 보유 종목 다음날 매수 감시 제외 | ✅ 완료 |
| 8 | universe.xlsx 일별 감시 기록 | ✅ 완료 |

#### Phase 4 후속 완료 (2026-05-26)

**history_cache + dual 유니버스** ✅
- bootstrap: 1714종 `data/history_cache/` (git 제외)
- 일일: page1 merge → RS/피처 → s1/s2 캐시 → `universe.xlsx`
- Naver IP: delay + jitter + 50종 batch pause + 재시도

**보유 종목 매수 감시 제외** ✅
- **당일**: `watchlist_symbols()` — `bought=True` 종목 제외 (기존)
- **다음날**: `prepare_universe()` 후 KIS 잔고 또는 SIM `trades.csv` FIFO로 보유 조회 → 매수 감시 제외, 트레일링 스탑은 유지
- **파일**: `core/open_positions.py`, `core/strategy.py` (`apply_open_position`), `main.py`

---

## 중요 메모
- KIS 요청 제한을 피하기 위해 예수금 조회는 캐시를 사용합니다.
- **history_cache**: bootstrap 1회 후 매일 page1 증분 (~12-17min). `data/history_cache/` (git 제외).
- **dual 유니버스**: `universe_cache_YYYYMMDD_s1.json` / `_s2.json`, `universe.xlsx` append.
- **운영 스케줄**:
  - PC **08:20** 기동 → `main.py` **08:30** → 감시 **09:00~15:30**
  - **15:32** result.xlsx → **15:35~** `vi_collector`, `gap_collector` → **15:40** 봇 종료
- `IS_PAPER_TRADING`(API)과 `SIM_MODE`(주문 시뮬)는 **별개** 설정.
- **KIS 토큰**: `data/kis_token_cache.json` (main/vi_collector 공유, git 제외).
- `data/`는 git에 포함하지 않습니다 (로그·캐시·결과 파일).
- `RESULT_WRITE_HHMM=15:32`, `SHUTDOWN_HHMM=15:40`.
- **현재 운영**: `IS_PAPER_TRADING=false` + `SIM_MODE=true`. 실매매 전환 시 `SIM_MODE=false`.
- **실매매 전환 전**: `main.py`의 `max_positions` 상한 복원 필요 (n+15 추적용 임시 해제).
- **gap_backfill**: `gap_collector`가 당일 Naver 백필·ticks 저장. 과거 소급은 `run --execute`. **KIS gap_collector가 일일 기준**.
- **익일시가매도수익률**: 매수 다음날 `history_cache`에 익일 시가 생기면 `gap_collector`가 xlsx 빈칸 자동 보정.
- **Naver fchart**: ~5~7거래일만. `rebuild_gap_backfill_xlsx`로 캐시 재시뮬 가능.
- **갭 시뮬 정규장 규칙**: 09:00~15:30만 사용, 종가 매도 15:30:00.
