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
│   ├── universe_cache.py     # 당일 유니버스 캐시
│   ├── naver_universe.py     # 네이버 종목 스크래핑
│   ├── naver_symbol_master.py
│   ├── result_csv.py
│   └── symbol_resolver.py
├── scripts/
│   ├── build_result.py
│   └── update_symbol_master.py
├── data/
│   └── logs/
├── tests/
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
python main.py
```

## 테스트
```bash
python -m pytest -q
```

---

## 중요 메모
- KIS 요청 제한을 피하기 위해 예수금 조회는 캐시를 사용합니다.
- 네이버 스크래핑은 약 1~2분 걸릴 수 있습니다.
- 상대강도 필터(6개월 수익률 배치)는 KIS API로 처리, 한계 시 네이버 증권 스크래핑으로 전환합니다.
- `data/`는 기본적으로 git에 포함하지 않습니다 (로그·캐시·결과 파일 등).
