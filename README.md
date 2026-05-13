# moa (모아) 자동매매 시스템

한국 주식(KOSPI/KOSDAQ) 대상 자동매매 프로젝트입니다.  
증권사: **한국투자증권 (KIS OpenAPI)**

## 대화 규칙 (중요)
- 이 저장소 작업 중 AI 응답은 **반드시 한국어 존댓말**로만 진행합니다. (반말 금지)
- 동일 요청을 반복하지 않도록, 합의된 요구사항/규칙은 문서(README/.cursorrules)에 명시된 내용을 우선합니다.

## 디렉토리
```text
moa/
├── config/
│   ├── settings.py
│   └── korea_market_holidays.txt
├── core/
│   ├── api_client.py
│   ├── logger.py
│   ├── order.py
│   ├── strategy.py
│   ├── trading_day.py
│   ├── naver_universe.py
│   ├── naver_symbol_master.py
│   ├── result_csv.py
│   └── universe_cache.py
├── scripts/
│   ├── build_result.py
│   └── update_symbol_master.py
├── data/
│   └── logs/
├── tests/
│   ├── test_strategy.py
│   ├── test_api_client.py
│   ├── test_naver_universe.py
│   └── test_trading_day.py
├── .cursorrules
├── .env.example
├── main.py
└── requirements.txt
```

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
ACCOUNT_PRDT_CD=01
IS_PAPER_TRADING=true
```

## 실행
```bash
python main.py
```

## 테스트
```bash
python -m pytest -q
```

## 형상관리
- `data/`는 기본적으로 git에 포함하지 않습니다(로그·캐시·결과 파일 등).
