#!/usr/bin/env bash
# VM에서 배포 (git pull + deps + restart). GitHub Actions 또는 수동 실행.
# sudo bash /opt/btc_forwardtest/scripts/vm_deploy.sh
set -euo pipefail

APP_DIR="/opt/btc_forwardtest"
BRANCH="${DEPLOY_BRANCH:-main}"

cd "$APP_DIR"

echo "[deploy] fetch origin/$BRANCH..."
git fetch origin "$BRANCH"
git reset --hard "origin/$BRANCH"

echo "[deploy] pip install..."
.venv/bin/pip install -q -r requirements.polymarket.txt

echo "[deploy] restart polymarket-worker..."
systemctl restart polymarket-worker
sleep 2
systemctl is-active --quiet polymarket-worker && echo "[deploy] OK" || {
  journalctl -u polymarket-worker -n 30 --no-pager
  exit 1
}
