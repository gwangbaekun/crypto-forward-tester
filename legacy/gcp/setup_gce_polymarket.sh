#!/usr/bin/env bash
# GCP e2-micro (Always Free) — Polymarket 워커 VM 생성
# 실행: bash scripts/setup_gce_polymarket.sh
# 선행: gcloud auth login, 결제 계정 연결(OPEN=True)
set -euo pipefail

# ── 수정 가능 ─────────────────────────────────────────────
PROJECT_ID="still-catalyst-474914-t7"
ZONE="asia-northeast1-b"
INSTANCE_NAME="polymarket-worker"
MACHINE_TYPE="e2-micro"
REPO="git@github.com:gwangbaekun/crypto-forward-tester.git"
# ─────────────────────────────────────────────────────────

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  프로젝트: $PROJECT_ID"
echo "  VM: $INSTANCE_NAME ($ZONE, $MACHINE_TYPE)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

gcloud config set project "$PROJECT_ID"

echo ""
echo "[1/3] Compute API 활성화..."
gcloud services enable compute.googleapis.com --quiet

echo ""
echo "[2/3] SSH 방화벽..."
gcloud compute firewall-rules create allow-ssh-polymarket \
  --allow=tcp:22 \
  --target-tags=polymarket-worker \
  --description="SSH for polymarket worker VM" \
  --quiet 2>/dev/null || echo "  → 이미 존재, 스킵"

echo ""
echo "[3/3] e2-micro VM 생성..."
gcloud compute instances create "$INSTANCE_NAME" \
  --zone="$ZONE" \
  --machine-type="$MACHINE_TYPE" \
  --image-family=debian-12 \
  --image-project=debian-cloud \
  --boot-disk-size=30GB \
  --boot-disk-type=pd-standard \
  --tags=polymarket-worker \
  --scopes=default,cloud-platform \
  --quiet 2>/dev/null && CREATED=1 || CREATED=0

if [ "$CREATED" -eq 0 ]; then
  echo "  → VM이 이미 있거나 생성 실패. 기존 인스턴스 사용."
fi

EXTERNAL_IP=$(gcloud compute instances describe "$INSTANCE_NAME" \
  --zone="$ZONE" --format='get(networkInterfaces[0].accessConfigs[0].natIP)')

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✅ VM 준비됨  (IP: $EXTERNAL_IP)"
echo "  ZONE: $ZONE  INSTANCE: $INSTANCE_NAME"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
