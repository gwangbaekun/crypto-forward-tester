#!/usr/bin/env bash
# VM 최초 1회 — GitHub에서 clone + 설치
# 선행: /root/.ssh/id_ed25519 에 GitHub Deploy Key (read) 등록
# 실행: sudo bash scripts/vm_bootstrap_github.sh
set -euo pipefail

REPO="${REPO_URL:-git@github.com:gwangbaekun/crypto-forward-tester.git}"
APP_DIR="/opt/btc_forwardtest"
BRANCH="${DEPLOY_BRANCH:-main}"

if [ "$(id -u)" -ne 0 ]; then
  echo "root 로 실행: sudo bash $0"
  exit 1
fi

apt-get update -qq
apt-get install -y -qq git

mkdir -p /root/.ssh
chmod 700 /root/.ssh
ssh-keyscan -t ed25519 github.com >> /root/.ssh/known_hosts 2>/dev/null || true

if [ ! -f /root/.ssh/id_ed25519 ]; then
  echo "❌ /root/.ssh/id_ed25519 없음"
  echo "   1) 로컬: ssh-keygen -t ed25519 -f pm-github-deploy -N \"\""
  echo "   2) GitHub repo → Settings → Deploy keys → pm-github-deploy.pub 추가"
  echo "   3) gcloud compute scp pm-github-deploy polymarket-worker:/root/.ssh/id_ed25519 --zone=us-central1-a"
  echo "   4) VM: chmod 600 /root/.ssh/id_ed25519 && 이 스크립트 다시 실행"
  exit 1
fi
chmod 600 /root/.ssh/id_ed25519

if [ -d "$APP_DIR/.git" ]; then
  echo "→ 이미 clone 됨: $APP_DIR"
  cd "$APP_DIR"
  git fetch origin "$BRANCH"
  git reset --hard "origin/$BRANCH"
else
  rm -rf "$APP_DIR"
  GIT_SSH_COMMAND="ssh -i /root/.ssh/id_ed25519 -o IdentitiesOnly=yes" \
    git clone --branch "$BRANCH" "$REPO" "$APP_DIR"
fi

bash "$APP_DIR/scripts/vm_install_polymarket.sh"
