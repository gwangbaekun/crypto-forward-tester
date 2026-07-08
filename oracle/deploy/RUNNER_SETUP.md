# 오사카 VM — GitHub Actions self-hosted runner 자동 배포

relay 코드(`oracle/relay/` + 의존 모듈 `executor.py`/`live.py`)가 main 에 push 되면
`.github/workflows/deploy-relay.yml` 이 **VM 에 등록된 self-hosted runner** 에서 실행돼 재빌드한다.

- **폴링 없음** — push 이벤트가 즉시 트리거(옛 systemd timer 방식 제거).
- **인바운드 SSH 불필요** — runner 가 GitHub 로 아웃바운드 롱폴. 포트 22 를 GitHub 클라우드에 열 필요 없음.
- 실주문 개인키(POLYMARKET_PK 등)는 VM 밖으로 나가지 않는다.

정보:
- VM: `ubuntu@161.33.28.23`
- repo: `gwangbaekun/crypto-forward-tester`
- 트리거 경로: `oracle/relay/**` + `_data/{executor,live,__init__}.py` (그 외 커밋엔 실행 안 함)

---

## 최초 1회 설정 (VM 에서)

### 1. self-hosted runner 등록
GitHub → repo → **Settings → Actions → Runners → New self-hosted runner (Linux x64)** 가
안내하는 명령을 실행. 등록 시 **라벨에 `oracle-relay` 추가**(워크플로가 이 라벨로 러너를 지정).

```bash
mkdir -p ~/actions-runner && cd ~/actions-runner
curl -o actions-runner.tar.gz -L <위 페이지가 준 다운로드 URL>
tar xzf actions-runner.tar.gz
./config.sh --url https://github.com/gwangbaekun/crypto-forward-tester \
  --token <위 페이지가 준 등록 토큰> --labels oracle-relay --unattended
```

### 2. RELAY_API_KEY(.env) 배치 — 워크플로가 절대 안 지움
```bash
mkdir -p ~/oracle-relay
echo "RELAY_API_KEY=<기존 키>" > ~/oracle-relay/.env    # 이미 있으면 skip
```

### 3. runner 를 서비스로 상시 가동
```bash
cd ~/actions-runner
sudo ./svc.sh install
sudo ./svc.sh start
```

### 4. runner 유저가 docker 를 sudo 없이 쓰게 (권장)
```bash
sudo usermod -aG docker "$USER"     # 이후 서비스 재시작(sudo ./svc.sh stop && start)
```

---

## 이후 운영

oracle 파일을 main 에 push → 워크플로 자동 실행. 확인:
- GitHub → repo → **Actions → deploy-oracle-relay** (실행/빌드 로그)
- VM: `docker logs -f polymarket-oracle-relay`, `curl -s localhost:9090/health` (`{"mode":"live"}` 여야 실주문)

**수동 재배포:** Actions → deploy-oracle-relay → **Run workflow**.

**대체(러너 없이):** 로컬에서 `bash oracle/deploy/deploy_relay.sh` (SSH scp → docker compose).

fade 엔진 등 oracle 무관 커밋은 워크플로가 트리거되지 않는다(불필요 재시작 방지).
