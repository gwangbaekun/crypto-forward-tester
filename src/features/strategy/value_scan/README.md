# Value Scan — Score Dashboard

KOSPI 200 + S&P 500 전 종목을 대상으로 **5개 팩터 종합 스코어(0~100점)**를 산출하여 BUY / HOLD / SELL 시그널을 생성하는 퀀트 스캔 엔진.

---

## 데이터 소스

| 시장 | 기본 데이터 | 보완 데이터 |
|------|------------|------------|
| **NASDAQ / S&P 500** | yfinance (전체) | — |
| **KOSPI 200** | Naver Mobile API (PER, PBR, EPS) | yfinance `.KS` (Quality/Health/Growth/Sentiment) |

> KOSPI는 Naver가 한국 밸류에이션 데이터를 더 정확히 제공하므로 PER·PBR은 Naver 우선.  
> ROE·D/E 등 재무 지표는 yfinance `.KS`로 보완. 소형주는 yfinance 커버리지가 없을 수 있음.

---

## 종합 스코어 구조 (100점 만점)

```
Composite Score (100)
├── Valuation   30점  — 얼마나 싸게 사는가
├── Quality     30점  — 얼마나 잘 버는 기업인가
├── Health      20점  — 재무 안전성
├── Growth      15점  — 성장하고 있는가
└── Sentiment    5점  — 시장 컨센서스
```

---

## 팩터별 지표 상세

### 1. Valuation (밸류에이션) — 30점

#### PER / 섹터 중앙값 비율 (최대 15pt)

PER(주가수익비율)을 동일 섹터 기업들의 중앙값과 비교.  
같은 섹터 내에서 상대적으로 얼마나 저평가되어 있는지를 측정.

| PER / 섹터 중앙값 | 점수 |
|-----------------|------|
| ≤ 0.45× | 15pt |
| ≤ 0.65× | 11pt |
| ≤ 0.85× |  7pt |
| ≤ 1.00× |  3pt |
| ≤ 1.20× |  1pt |
| > 1.20×  |  0pt |

> **주의**: PER이 낮아도 적자 기업(EPS ≤ 0)이면 Quality/Health 점수에서 깎임.

#### EV/EBITDA (최대 9pt)

기업 전체 가치(부채 포함)를 영업이익(EBITDA)으로 나눈 비율.  
PER과 달리 부채 구조와 감가상각 영향을 제거한 실질 밸류에이션 지표.

| EV/EBITDA | 점수 |
|-----------|------|
| < 8× | 9pt |
| < 12× | 6pt |
| < 18× | 3pt |
| < 25× | 1pt |
| ≥ 25× | 0pt |

> 업종 특성에 따라 절대값 해석이 다름. IT 성장주는 30×도 정상 범위일 수 있음.

#### PEG Ratio (최대 6pt)

PEG = Forward PER ÷ EPS 성장률.  
성장률을 감안한 밸류에이션 지표. PEG < 1이면 성장 대비 저평가.

| PEG | 점수 |
|-----|------|
| < 0.8 | 6pt |
| < 1.2 | 4pt |
| < 1.8 | 2pt |
| ≥ 1.8 | 0pt |

> EPS 성장률 데이터가 없으면 계산 불가(0pt). PLTR 같은 고성장주는 PEG < 2여도 정당화 가능.

---

### 2. Quality (수익성) — 30점

기업이 자본을 얼마나 효율적으로 사용해 이익을 내는지 측정. Value Trap(저PER 부실주) 필터링에 핵심.

#### ROE — Return on Equity (최대 12pt)

자기자본이익률. 주주 돈으로 얼마나 버는지.  
ROE = 순이익 ÷ 자기자본 × 100

| ROE | 점수 |
|-----|------|
| ≥ 30% | 12pt |
| ≥ 20% |  9pt |
| ≥ 12% |  6pt |
| ≥  5% |  2pt |
| < 5% (음수 포함) |  0pt |

> 금융주(은행)는 레버리지 특성상 ROE가 높게 나오는 경향. 섹터 맥락 고려 필요.

#### Operating Margin (영업이익률) — 최대 10pt

영업이익 ÷ 매출 × 100. 본업에서 얼마나 남기는지.

| Op. Margin | 점수 |
|-----------|------|
| ≥ 35% | 10pt |
| ≥ 20% |  7pt |
| ≥ 10% |  4pt |
| ≥  0% |  1pt |
| < 0%  |  0pt |

> 소프트웨어(SaaS) 35%+, 반도체 20%+, 유통 3~5% — 섹터마다 기준이 다름.

#### ROA — Return on Assets (최대 8pt)

총자산이익률. 보유 자산 전체로 얼마나 버는지. 부채 효과 제거한 순수 자산 효율성.

| ROA | 점수 |
|-----|------|
| ≥ 15% | 8pt |
| ≥  8% | 5pt |
| ≥  0% | 2pt |
| < 0%  | 0pt |

---

### 3. Health (재무 건전성) — 20점

재무 위기 가능성을 측정. **Health ≤ 4pt이면 점수와 무관하게 SELL 판정.**

#### D/E Ratio (부채비율) — 최대 10pt

Debt-to-Equity. 부채가 자기자본의 몇 배인지.  
yfinance 기준: 값이 퍼센트로 반환됨 (150 = 150%).

| D/E | 점수 | 해석 |
|-----|------|------|
| < 0% (순현금) | 10pt | 빚보다 현금이 많음 |
| < 30% | 10pt | 매우 건전 |
| < 80% | 7pt | 정상 범위 |
| < 150% | 3pt | 레버리지 있음 |
| ≥ 150% | 0pt | 고부채 |
| > 400% | **SELL 강제** | 재무위기 위험 |

#### Current Ratio (유동비율) — 최대 6pt

유동자산 ÷ 유동부채. 단기 채무 상환 능력. 1 미만이면 단기 유동성 위험.

| Current Ratio | 점수 |
|--------------|------|
| ≥ 3.0 | 6pt |
| ≥ 2.0 | 5pt |
| ≥ 1.5 | 3pt |
| ≥ 1.0 | 1pt |
| < 1.0 | 0pt |

#### FCF (잉여현금흐름) — 4pt

Free Cash Flow = 영업현금흐름 - 자본지출.  
FCF가 양수면 4pt, 음수면 0pt. (단순 이진 판정)

> FCF 음수 = 사업에 계속 투자 중이라는 의미일 수도 있음. 성장 초기 기업은 고려 필요.

---

### 4. Growth (성장성) — 15점

#### Revenue Growth (매출 성장률) — 최대 8pt

전년 동기 대비 매출 증가율.

| Rev Growth | 점수 |
|-----------|------|
| ≥ 25% | 8pt |
| ≥ 15% | 6pt |
| ≥  5% | 3pt |
| ≥  0% | 1pt |
| < 0%  | 0pt |

#### EPS Growth (순이익 성장률) — 최대 7pt

전년 동기 대비 주당순이익 증가율.

| EPS Growth | 점수 |
|-----------|------|
| ≥ 25% | 7pt |
| ≥ 15% | 5pt |
| ≥  5% | 2pt |
| < 5%  | 0pt |

> EPS Growth가 음수여도 Revenue Growth가 높으면 투자 국면일 수 있음.

---

### 5. Sentiment (시장 컨센서스) — 5점

#### Analyst Recommendation (애널리스트 컨센서스)

yfinance `recommendationMean`: 1.0 (Strong Buy) ~ 5.0 (Strong Sell)

| Rec. 값 | 의미 | 점수 |
|---------|------|------|
| ≤ 1.5 | Strong Buy | 5pt |
| ≤ 2.0 | Buy | 4pt |
| ≤ 2.5 | Hold (매수 우위) | 3pt |
| ≤ 3.0 | Hold | 1pt |
| > 3.0 | Sell/Strong Sell | 0pt |

> 전체 배점의 5%만 차지. 애널리스트 컨센서스는 후행 지표이므로 가중치를 낮게 둠.

---

## BUY / SELL / HOLD 판정 기준

```
SELL (강제 — 점수 무관):
  ├── D/E > 400%          → 과도한 레버리지
  └── ROE < -15%          → 지속적 대규모 손실

SELL (점수 기반):
  ├── Composite ≤ 28점
  └── Health ≤ 4점        → 재무건전성 최소 기준 미달

BUY:
  ├── Composite ≥ 65점
  ├── Quality ≥ 14점      → 최소 수익성 확보
  └── Health ≥ 8점        → 최소 재무건전성 확보

HOLD: 나머지 전부
```

---

## 스코어 해석 가이드

| 점수 | 색상 | 의미 |
|------|------|------|
| 65+ | 🟢 초록 | BUY 후보. 밸류·퀄리티·건전성 종합 우수 |
| 45~64 | 🟡 노랑 | HOLD. 일부 팩터 미흡 — 모니터링 |
| 0~44 | 🔴 빨강 | SELL 후보 또는 리스크 높음 |

### 케이스 해석 예시

**PLTR (Palantir) — Score 72, BUY**
- Valuation: 0/30 (PER 180+, 섹터 대비 5× 고평가)
- Quality: 28/30 (ROE 30%+, Op.Margin 20%+)
- Health: 19/20 (무부채, FCF 양수)
- Growth: 14/15 (매출 35%+, EPS 80%+)
- Sentiment: 4/5 (Buy 컨센서스)

→ 비싸지만 퀄리티·성장이 압도적. 이 시스템은 **"비싼 퀄리티 성장주"도 BUY** 가능.

**Score 50, SELL 케이스**
- D/E 500% (재무위기 강제 SELL) — 다른 팩터가 좋아도 SELL

---

## KOSPI vs NASDAQ 차이점

| 항목 | KOSPI | NASDAQ/S&P 500 |
|------|-------|---------------|
| PER/PBR 소스 | Naver Mobile API | yfinance |
| ROE·Margin·D/E | yfinance `.KS` | yfinance |
| 섹터 분류 | KRX 섹터 (한국어) | GICS 섹터 (영어) |
| Forward 데이터 | Naver 컨센서스 (`cnsPer`, `cnsEps`) | yfinance `forwardPE` |
| 커버리지 | KOSPI 200 | S&P 500 전체 |
| yfinance 커버리지 | 대형주 11/13 필드 ✓, 소형주 누락 가능 | 대부분 완전 커버 |

---

## API 엔드포인트

| Method | Path | 설명 |
|--------|------|------|
| `POST` | `/quant/value_scan/scan?market=nasdaq` | 스캔 트리거 (nasdaq/kospi/all) |
| `GET`  | `/quant/value_scan/scan/results?market=nasdaq` | 최신 스캔 결과 rows |
| `GET`  | `/quant/value_scan/scan/status` | 스캔 실행 상태·스케줄 |
| `GET`  | `/quant/value_scan/positions` | 오픈 포지션 + 실시간 P&L |
| `GET`  | `/quant/value_scan/history` | 청산 이력 |
| `GET`  | `/quant/value_scan/stats` | 포트폴리오 통계 |
| `GET`  | `/quant/value_scan/benchmark?market=nasdaq` | SPY/KS11 vs 포트폴리오 비교 |
| `GET`  | `/quant/value_scan/famous` | Famous watchlist 매칭 |

---

## 스코어 컴포넌트 JSON 예시

```json
{
  "symbol": "AAPL",
  "market": "nasdaq",
  "sector": "Technology",
  "rating": "BUY",
  "score": 78,
  "score_valuation": 18,
  "score_quality": 27,
  "score_health": 17,
  "score_growth": 12,
  "score_sentiment": 4,
  "per": 28.4,
  "sector_median": 36.2,
  "roe": 147.2,
  "roa": 28.1,
  "op_margin": 31.5,
  "net_margin": 25.3,
  "d_e": 174.0,
  "current_ratio": 1.04,
  "fcf": 99300000000,
  "rev_growth": 2.8,
  "eps_growth": 10.1,
  "ev_ebitda": 22.3,
  "analyst_rec": 1.9,
  "target_upside": 14.2
}
```

---

## 한계점 및 주의사항

1. **후행 데이터**: yfinance / Naver 데이터는 분기 보고서 기준. 최근 실적 급변은 반영 안 될 수 있음.
2. **섹터 편향**: 유틸리티·금융은 업종 특성상 PER·ROE 기준이 다름. 섹터 내 상대 비교로 일부 완화됨.
3. **성장주 고평가 허용**: 퀄리티+성장이 높으면 비싼 주식도 BUY 가능. "가치주 필터"만 원하면 Valuation < 20pt 조건 추가 권장.
4. **KOSPI 소형주**: yfinance `.KS` 커버리지 부재 시 Quality/Health/Growth 점수가 0이 될 수 있음. Health 0pt → SELL 강제 주의.
5. **스캔 주기**: 일 1회 권장. 장중 데이터는 yfinance 실시간 지원 안 됨.
