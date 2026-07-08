# oracle relay

한국/Railway IP에서 막힌 Polymarket buy/sell을 오사카(Oracle Cloud) VM 경유로
**실제 서명·전송**하는 릴레이. 주문 로직은 검증된 `executor.place_order`
(py-clob-client-v2)를 그대로 재사용한다.

fade 엔진(`src/features/strategy/polymarket/fade/oracle_client.py`)이 스파이크
진입/청산 시 `POST /order` 를 호출하면 릴레이가 Polymarket에 주문을 넣는다.

## 엔드포인트

- `GET /health` — live/sim 모드
- `GET /balance` — 가용 pUSD (CLOB COLLATERAL)
- `POST /order` — `{action:buy|sell, token_id, price, size_usd, size_shares?}` → 주문 실행
- `POST /redeem?token_id=` — 만기 해소 포지션 gasless redeem

## env (`oracle/relay/.env`, compose 자동 로드)

```
RELAY_API_KEY=<필수. X-Relay-Key 헤더로 검증. 없으면 누구나 주문 가능>
```

> 주문 명목가 하드캡(RELAY_MAX_ORDER_USD)은 제거됨 — 사이징은 fade config.yaml(full/fixed)이 결정.

Polymarket 자격증명(POLYMARKET_PK 등)은 이미지에 굽지 않고 호스트
`/etc/btc-forwardtest.env` 를 read-only 마운트해서 읽는다. `POLYMARKET_LIVE=true`
여야 실제 주문이 나간다(아니면 executor가 skip).

## 배포

**자동(권장):** relay 파일을 GitHub main에 push하면 GitHub Actions
(`.github/workflows/deploy-relay.yml`)가 VM의 self-hosted runner에서 재빌드한다.
push 이벤트 즉시 트리거(폴링 아님). 최초 1회 러너 설정은 **`oracle/deploy/RUNNER_SETUP.md`** 참고.

**수동(대체):**
```bash
bash oracle/deploy/deploy_relay.sh    # 로컬 → VM scp → docker compose up --build
docker logs -f polymarket-oracle-relay
```

## 호출측(Railway) 연결

Railway 환경변수에 아래를 넣으면 fade 엔진이 이 릴레이로 실주문을 보낸다
(미설정이면 sim — 실주문 없이 로그만):

```
ORACLE_RELAY_URL=http://<VM-IP>:9090
ORACLE_RELAY_KEY=<RELAY_API_KEY 와 동일>
```

⚠️ 실주문 트레이더는 한 곳만(Railway). 로컬과 동시에 켜면 같은 지갑에 이중 주문.
