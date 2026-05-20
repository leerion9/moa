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
│   ├── naver_universe.py     # 네이버 종목 스크래핑
│   ├── naver_symbol_master.py
│   ├── result_csv.py
│   └── symbol_resolver.py
├── scripts/
│   ├── build_result.py
│   ├── build_universe.py     # 유니버스 배치 빌드 CLI
│   └── update_symbol_master.py
├── data/
│   └── logs/
├── tests/
│   ├── test_api_client.py    # 32개 테스트
│   ├── test_universe_builder.py  # 38개 테스트
│   ├── test_naver_universe.py
│   ├── test_strategy.py
│   ├── test_result_fifo.py
│   └── test_trading_day.py
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
STRATEGY_MODE=1         # 1=최초돌파, 2=추세지속
TRAILING_STOP_PCT=0.075
```

## 실행
```bash
# 장 시작 전 유니버스 배치 빌드 (08:00~08:50 권장)
python -m scripts.build_universe

# 매매 봇 실행
python main.py
```

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
  1. 시총 목록 수집 (KIS primary → Naver fallback)
  2. 1차 필터 (시총·ETF·우선주)
  3. 종목별 히스토리 수집 (KIS 배치)
  4. RS 필터 (6개월 수익률 상위 10%)
  5. 피처 계산 (w52_high, vol_ma20, w52_hit_60d, w52_hit_10d)
  6. 2차 필터 (전략 모드별)
- `naver_universe.py`에 `fetch_market_cap_list()` Naver fallback 추가

#### Phase 2-3: main.py 연결
- `prepare_universe()`에서 캐시 없을 때 `UniverseBuilder.build()` 자동 호출
- 빌드 성공 시 `save_cache()` 저장, 실패 시 에러 로그 후 빈 목록으로 계속

#### Phase 2-4: 배치 스크립트 (`scripts/build_universe.py`)
- CLI: `python -m scripts.build_universe [--date YYYYMMDD] [--strategy 1|2] [--force]`
- 캐시 존재 확인 → 종목 마스터 로드 → 빌드 → 저장 → 결과 출력

**테스트 현황**: 총 **70개 통과** (api_client 13, universe_builder 38, 기타 19)

### 🔴 Phase 3 — 미완료 (다음 작업 대상)

#### 3-1. ETF 필터 보완
- 현재: 종목 마스터(naver_symbol_master)가 있을 때만 ETF 이름 필터 동작
- 목표: 종목 마스터 없어도 KIS API의 종목 타입 코드(`iscd_stat_cls_code`) 등으로 ETF 판별
- 또는: `build_universe.py`가 항상 종목 마스터를 먼저 갱신하도록 강제

#### 3-2. `korea_market_holidays.txt` 연도별 자동 관리
- 현재: 2026년 평일 공휴일 목록 수동 관리
- 목표: KIS `get_holiday_info()` 활용하여 휴장일 자동 갱신 스크립트 추가

#### 3-3. 페이퍼 트레이딩 end-to-end 검증
- `.env` 설정(모의투자 계정) 후 `build_universe.py` → `main.py` 실제 실행
- 로그 파일 및 trades.csv 정상 기록 확인

#### 3-4. `on_quote()` 거래량 skip 로직 개선
- 현재: 거래량 미충족 시 `state.skip=True`로 **당일 영구 제외** → 오전에 거래량 부족해도 오후에 조건 충족 가능
- 개선안: skip 대신 매 tick마다 거래량 재확인

#### 3-5. 결과 분석 도구 검토
- `scripts/build_result.py`의 `append_result1_rows` import — `result_csv.py` 내 해당 함수 존재 여부 확인 필요

---

## 중요 메모
- KIS 요청 제한을 피하기 위해 예수금 조회는 캐시를 사용합니다.
- 네이버 스크래핑은 약 1~2분 걸릴 수 있습니다.
- 상대강도 필터(6개월 수익률 배치)는 KIS API로 처리, 한계 시 네이버 증권 스크래핑으로 전환합니다.
- `data/`는 기본적으로 git에 포함하지 않습니다 (로그·캐시·결과 파일 등).
- `scripts/build_universe.py`는 장 시작 전(08:00~08:50 KST) 실행 권장. 전 종목 히스토리 수집에 약 2~5분 소요 예상.
