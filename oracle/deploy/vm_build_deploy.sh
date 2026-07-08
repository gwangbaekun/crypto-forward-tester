#!/usr/bin/env bash
# GitHub Actions(오사카 VM의 self-hosted runner)가 호출 — 체크아웃된 repo에서 릴레이 빌드
# 컨텍스트를 스테이징하고 docker compose 재빌드한다. 폴링 없음 — main push 이벤트가 워크플로를
# 트리거한다(.github/workflows/deploy-relay.yml).
#
# BUILD_DIR/.env(RELAY_API_KEY, compose 가 자동 로드)는 절대 건드리지 않는다.
set -euo pipefail

# runner 는 repo 를 GITHUB_WORKSPACE 에 체크아웃한다. 로컬 수동 실행 시엔 스크립트 상대경로.
REPO_DIR="${GITHUB_WORKSPACE:-$(cd "$(dirname "$0")/../.." && pwd)}"
BUILD_DIR="${RELAY_BUILD_DIR:-$HOME/oracle-relay}"

RELAY="$REPO_DIR/oracle/relay"
SRC="$REPO_DIR/src"

echo "[vm-deploy] $(date -u +%FT%TZ) 스테이징 → $BUILD_DIR (repo=$REPO_DIR)"
mkdir -p "$BUILD_DIR"
rm -rf "$BUILD_DIR/src"    # 코드만 갱신 — .env(시크릿)는 보존
cp "$RELAY/app.py" "$RELAY/requirements.txt" "$RELAY/Dockerfile" "$RELAY/docker-compose.yml" "$BUILD_DIR/"

# executor/live 가 의존하는 패키지 트리만 복사 (DB/세션 등 무관 모듈 제외)
PKG="$BUILD_DIR/src/features/strategy/polymarket/_data"
mkdir -p "$PKG"
cp "$SRC/features/__init__.py"                           "$BUILD_DIR/src/features/__init__.py"
cp "$SRC/features/strategy/__init__.py"                  "$BUILD_DIR/src/features/strategy/__init__.py"
cp "$SRC/features/strategy/polymarket/__init__.py"       "$BUILD_DIR/src/features/strategy/polymarket/__init__.py"
cp "$SRC/features/strategy/polymarket/_data/__init__.py" "$PKG/__init__.py"
cp "$SRC/features/strategy/polymarket/_data/executor.py" "$PKG/executor.py"
cp "$SRC/features/strategy/polymarket/_data/live.py"     "$PKG/live.py"

echo "[vm-deploy] docker compose up -d --build"
cd "$BUILD_DIR"
docker compose up -d --build || sudo docker compose up -d --build

sleep 3
echo -n "[vm-deploy] /health → "
curl -s localhost:9090/health || true
echo
echo "[vm-deploy] 완료"
