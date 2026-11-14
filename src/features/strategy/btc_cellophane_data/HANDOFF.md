# btc_cellophane_data — backtest 에서 넘겨받은 내용

2026-11-14 작성. 설정값은 전략 폴더 2개의 `config.yaml` 에 있다: `lsr_sell_cmeoi_btc_cellophane_a/`, `lsr_sell_cmeoi_btc_cellophane_b/`. 이 폴더는 두 전략이 같이 쓰는 데이터 코드다. 이 문서는 규칙과 남은 일을 적는다.

## 1. 무엇인가

BTCUSDT 5분봉 전략 2개다. 이름은 `lsr_sell_cmeoi_btc_cellophane_a`, `lsr_sell_cmeoi_btc_cellophane_b`.

이름의 뜻: lsr = long/short 계좌 비율, sell = 현물 매도량, cmeoi = Binance 와 CME 의 미결제약정 차이.

- long: 급락 뒤 반등을 산다.
- short: Binance 의 미결제약정 (OI) 이 CME 보다 갑자기 크게 불었을 때 판다.
- 계좌 1개에 포지션 1개만 둔다. 반대 신호가 오면 뒤집는다.

출처: `backtest_quant/src/strategies/crypto/cellophane_5m_same_direction_overlap/`
기준 trade 목록: 같은 폴더의 `saved_pairs/2026-11-14_A.json`, `2026-11-14_B.json`

## 2. 신호 규칙

### 2.1 z 계산 (장 1개)

장 (sheet) = 지표 1개와 그 설정 (`term`, `c_in`).

```
med = 최근 term 개 값의 중앙값
mad = 최근 term 개의 |값 − 그 시점의 med| 의 중앙값 × 1.4826
z   = (값 − med) ÷ mad        (mad 가 0 이면 z 는 없음)
```

- backtest 코드: `crypto_cellophane/grid/criteria.py` 의 `robust_z`.
- mad 는 과거 term 개 시점 각각의 med 를 쓴다. 그래서 z 1개에 값이 `2 × term − 1` 개 필요하다.
- `term` = 288 (5분봉 1일) × scale. `config.yaml` 에는 곱한 결과를 적었다.

### 2.2 지표 4개

| id | 계산 | 원천 |
|---|---|---|
| `metrics.count_long_short_ratio` | long 계좌 수 ÷ short 계좌 수 | Binance 선물 `globalLongShortAccountRatio`, 5분 |
| `klines.spot.sell_volume` | volume − taker buy volume | Binance 현물 5분 klines |
| `rel.venue.binance_vs_cme_open_interest.diff` | Binance OI − CME OI | 아래 참고 |
| `rel.venue.binance_vs_cme_open_interest.ratio` | Binance OI ÷ CME OI | 아래 참고 |

- Binance OI = 선물 `sumOpenInterest` (BTC 수량), 5분.
- CME OI = `BTC.F.BITCOIN FUTURES.open_interest × 5 + MBT.F.MICRO BITCOIN FUTURES.open_interest × 0.1` (BTC 수량).
- 두 OI 는 5분 시각에 맞춘다. CME 는 그 시각까지 공개된 가장 최근 값을 쓴다 (7일 넘게 오래된 값은 쓰지 않는다).

### 2.3 진입

- 한 방향의 장 2개가 모두 `z >= c_in` 이면 그 방향이 "켜짐" 이다.
- 꺼짐 → 켜짐으로 바뀐 5분봉이 신호다. 다음 5분봉의 open 에 진입한다.
- `c_in` 은 backtest 가 2022-01-20 이후 전체 기간의 z 분포에서 정한 고정 숫자다. forward test 는 이 숫자를 다시 계산하지 않는다.

### 2.4 청산

- long: SL = 진입가 − 3%, TP = 진입가 + 4.5%.
- short: SL = 진입가 + 2%, TP = 진입가 − 3%.
- 한 봉에서 SL 과 TP 에 모두 닿으면 SL 로 처리한다.

### 2.5 포지션 1개 규칙

| 상태 | 일 |
|---|---|
| 포지션 없음 | 먼저 온 신호로 진입한다 |
| 보유 중, 같은 방향 신호 | 무시한다 |
| 보유 중, 반대 방향 신호 | 다음 5분봉 open 에 청산하고 (사유 `flip`) 같은 가격에 반대로 진입한다 |
| 같은 5분봉에 long 과 short 신호가 같이 옴 | 정한 규칙이 없다. backtest 는 error 로 처리한다 |

## 3. 흐르는 구조

데이터를 계속 쌓지 않는다. 지표마다 고정 길이의 최근 구간만 든다.

| 지표 | 가장 긴 term | 필요한 5분봉 수 (2 × term) |
|---|---|---|
| `count_long_short_ratio` | 1,152 | 2,304 (약 8일) |
| `spot.sell_volume` | 216 | 432 |
| OI diff | 432 | 864 |
| OI ratio | 144 | 288 |

- 시작할 때 이 길이만큼 REST 로 받아 채운다. 그 뒤에는 새 봉 1개가 오면 가장 오래된 봉 1개를 버린다.

## 4. forward test 에 지금 있는 것과 없는 것

| 데이터 | 상태 | 위치 |
|---|---|---|
| long/short 계좌 비율 | 부분. 15분 값 1개만 받는다 | `src/common/market_data.py` `fetch_binance_lsr` |
| 현물 klines (taker buy 포함) | 있음. 매번 새로 받는다 | `spot_perp_cvd/data_feed.py` `_fetch_spot_klines` |
| Binance OI | 있음. 매번 새로 받는다 | `src/common/binance_service.py` `get_open_interest` |
| CME OI | **없음** | — |

### CME OI 를 받는 방법 (backtest 와 같게)

- 원천: CME 공개 FTP `ftp.cmegroup.com` 의 `daily_volume_YYYYMMDD.xlsx`. 하루에 파일 1개.
- backtest 는 파일이 FTP 에 올라온 시각 (MDTM) 을 공개 시각으로 쓴다.
- 최근 400일의 공개 시각 (UTC): 08:31 과 09:31 이 대부분이고, 05:01 과 06:01 이 그다음이다 (미국 서머타임에 따라 1시간 차이).
- backtest 의 short 진입 중 많은 수가 이 시각 직후의 5분봉 (08:35, 09:35) 에 난다.
- 그래서 forward test 는 이 시각 무렵에 FTP 를 1분 간격으로 확인해야 한다. 파일을 늦게 받으면 진입 가격이 backtest 와 달라진다.
- backtest 코드: `backtest_quant/datalab/raw/cme/fetch.py`, `fetch_mdtm.py`, `datalab/parse/cme/parse.py`.

## 5. 구현할 때 정할 것

1. 태그 2개를 돌리는 방법. 지금 forward test 에는 한 전략을 설정 2개로 같이 돌리는 길이 없다. engine class 2개로 만드는 것이 가장 단순하다.
2. 처음에는 주문 없이 기록만 한다: `binance_live: false`, `ctrader_live: false`.
3. 같은 5분봉에 long 과 short 신호가 같이 올 때의 규칙.
4. FTMO 의 하루 손실 한도 (5%) 에서 주문을 막는 장치. backtest 의 가장 나쁜 하루는 −4.0% 다 (1배 크기).

## 6. backtest 기준 값 (ftmo 수수료: 왕복 0.065% + swap 하루 0.0833%)

| | a | b |
|---|---|---|
| 기간 | 2022-02-17 ~ 2026-11-04 | 2022-02-17 ~ 2026-10-22 |
| trades | 111 | 84 |
| PF | 1.78 | 2.22 |
| comp | 124.8% | 148.5% |
| MDD | 7.2% | 8.3% |
| 1년당 거래 | 약 24 | 약 18 |

- 이 값은 436,590개 설정과 90,000개 쌍에서 고른 것이다. forward test 에서는 더 낮게 나올 것으로 본다.
