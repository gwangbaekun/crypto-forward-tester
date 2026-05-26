#!/usr/bin/env bash
# VM 안에서 실행 — Python venv + systemd 등록
# sudo bash /opt/btc_forwardtest/scripts/vm_install_polymarket.sh
set -euo pipefail

APP_DIR="/opt/btc_forwardtest"
ENV_FILE="/etc/btc-forwardtest.env"

if [ "$(id -u)" -ne 0 ]; then
  echo "root 로 실행하세요: sudo bash $0"
  exit 1
fi

cd "$APP_DIR"

echo "[1/5] 시스템 패키지..."
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git libpq-dev build-essential

echo ""
echo "[2/5] Python venv + 의존성..."
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.polymarket.txt -q

echo ""
echo "[3/5] 환경 변수 파일..."
if [ ! -f "$ENV_FILE" ]; then
  if [ -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    echo "  → $APP_DIR/.env 를 $ENV_FILE 로 복사함"
  else
    cat > "$ENV_FILE" <<'EOF'
# Railway PostgreSQL Public URL (host.docker.internal 은 VM에서 동작 안 함)
DATABASE_URL=postgresql://USER:PASS@HOST:PORT/DB

POLYMARKET_WALLET_ADDRESS=
POLYMARKET_EOA_ADDRESS=
POLYMARKET_API_KEY=
POLYMARKET_API_SECRET=
POLYMARKET_PASSPHRASE=
POLYMARKET_PK=
POLYMARKET_SIGNATURE_TYPE=3
POLYMARKET_LIVE=false
FRED_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
TELEGRAM_DISABLE=0
EOF
    chmod 600 "$ENV_FILE"
    echo "  ⚠️  $ENV_FILE 템플릿 생성됨 — DATABASE_URL 등 수정 후:"
    echo "      sudo systemctl restart polymarket-worker"
  fi
else
  echo "  → $ENV_FILE 이미 존재, 유지"
fi

echo ""
echo "[4/5] systemd 서비스 등록..."
cp "$APP_DIR/deploy/polymarket-worker.service" /etc/systemd/system/polymarket-worker.service
systemctl daemon-reload
systemctl enable polymarket-worker

echo ""
echo "[5/5] 서비스 시작..."
systemctl restart polymarket-worker
sleep 2
systemctl status polymarket-worker --no-pager || true

echo ""
echo "완료. 로그: journalctl -u polymarket-worker -f"
