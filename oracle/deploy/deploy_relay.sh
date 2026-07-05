#!/usr/bin/env bash
# 오라클 VM에 실주문 릴레이 배포.
#   - executor/live 패키지 트리를 릴레이 빌드 컨텍스트로 스테이징
#   - VM 으로 rsync 후 docker compose up --build
#
# 사용: bash oracle/deploy/deploy_relay.sh
set -euo pipefail

REPO="/Users/home/Developer/T/btc_forwardtest"
SRC="$REPO/src"
RELAY="$REPO/oracle/relay"
VM_HOST="ubuntu@161.33.28.23"
SSH_KEY="$HOME/.ssh/oracle_polymarket"
STAGE="$(mktemp -d)/relay"

echo "[1/4] 빌드 컨텍스트 스테이징 → $STAGE"
mkdir -p "$STAGE"
cp "$RELAY/app.py" "$RELAY/requirements.txt" "$RELAY/Dockerfile" "$RELAY/docker-compose.yml" "$STAGE/"

# executor/live 가 의존하는 패키지 트리만 복사 (DB/세션 등 무관 모듈 제외)
PKG="$STAGE/src/features/strategy/polymarket/_data"
mkdir -p "$PKG"
cp "$SRC/features/__init__.py"                              "$STAGE/src/features/__init__.py"
cp "$SRC/features/strategy/__init__.py"                     "$STAGE/src/features/strategy/__init__.py"
cp "$SRC/features/strategy/polymarket/__init__.py"          "$STAGE/src/features/strategy/polymarket/__init__.py"
cp "$SRC/features/strategy/polymarket/_data/__init__.py"    "$PKG/__init__.py"
cp "$SRC/features/strategy/polymarket/_data/executor.py"    "$PKG/executor.py"
cp "$SRC/features/strategy/polymarket/_data/live.py"        "$PKG/live.py"

echo "[2/4] VM 으로 전송 (~/oracle-relay) — .env(시크릿)는 보존"
# 코드/빌드 파일만 갱신, 기존 .env(RELAY_API_KEY 등)는 건드리지 않음
ssh -i "$SSH_KEY" "$VM_HOST" "mkdir -p ~/oracle-relay ~/oracle-relay/src && rm -rf ~/oracle-relay/src"
scp -i "$SSH_KEY" -r "$STAGE/." "$VM_HOST:~/oracle-relay/"

echo "[3/4] docker compose up --build"
ssh -i "$SSH_KEY" "$VM_HOST" "cd ~/oracle-relay && (docker compose up -d --build || sudo docker compose up -d --build)"

echo "[4/4] health 확인"
sleep 3
ssh -i "$SSH_KEY" "$VM_HOST" "curl -s localhost:9090/health"
echo
rm -rf "$(dirname "$STAGE")"
echo "완료."
