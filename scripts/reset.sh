#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."

echo "=== 컨테이너 중지 ==="
docker compose down

echo "=== DB 삭제 ==="
rm -f data/forwardtest.db

echo "=== Redis 볼륨 삭제 ==="
docker volume rm btc_forwardtest_redis_forwardtest 2>/dev/null || true

echo "=== 재시작 ==="
docker compose up -d --build

echo "=== 완료 ==="
