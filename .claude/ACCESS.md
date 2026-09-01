# 접근 게이트 (`src/features/auth/`)

> 이력 공개용. 전체 앱이 `AccessGateMiddleware` 뒤에 있다.
> 계기: 루트 `/` 가 `POST /api/strategies-master`(전략 YAML 저장)를 **무인증 노출** 중이었다.

---

## 1. 역할

| 역할 | 인증 | 권한 |
|---|---|---|
| admin | `ADMIN_PASSWORD` (env) | 전체. 세션 `AUTH_ADMIN_TTL_DAYS` (기본 30일) |
| guest | 발급된 패스코드 | **지정된 3개 대시보드 전용 · 읽기 전용.** 그 외 경로와 모든 쓰기 요청 403 |

- 게스트 허용 화면: Spot-Perp CVD, OI Accel Breakout v2, Polymarket Fade.
  루트 인덱스도 이 3개만 표시하고, 직접 URL 접근도 403 (`features.auth.policy`).
- 공개 경로: `/health` `/login` `/logout` `/static/*` `/favicon.ico` `/a/*`. 나머지 전부 게이트.
- 게스트를 GET-only 로 묶어 전략 YAML 저장·포지션 청산·DB 리셋·주문 API 를 전부 차단한다.

---

## 2. 세션

- HMAC-SHA256 서명 쿠키 (`ft_session`, HttpOnly, SameSite=Lax, https 면 Secure).
  **새 의존성 없음** — stdlib 만 쓴다.
- 게스트 코드는 평문 저장하지 않는다 — `access_passes.code_hash` (HMAC).
  발급 응답에서 **1회만** 노출된다.
- **매 요청마다 DB 를 재확인**한다 → 폐기·만료가 즉시 반영된다 (쿠키가 남아 있어도 차단).
- 관리 화면: `/admin/access` (발급·연장·폐기·사용이력).

---

## 3. 재발급 문제 — 두 경로

24h 고정이면 상대가 다음 날 잠기고 매번 새 코드를 보내야 한다.

1. **자동 연장 (sliding, 기본 ON)** — `sliding_hours` 설정 시 접속할 때마다
   `expires_at = now + sliding_hours`. 계속 보는 사람은 재발급이 필요 없고, 발길이 끊기면
   그 시점부터 24h 뒤 자동 소멸한다. `max_expires_at` 절대 상한(기본 14일)으로 무한 연장 방지.
   DB write 는 `_SLIDE_WRITE_THRESHOLD_SEC=60` 으로 스로틀. sliding 패스의 쿠키 TTL 은
   상한까지 길게 발급하되 **유효성 판단은 항상 DB** 다.
2. **수동 연장** — `POST /api/access/passes/{id}/extend` : **같은 코드를 유지**하고 만료만 +N시간.
   이미 만료된 코드도 되살린다. 폐기된 것은 불가.

---

## 4. 전달 — 매직 링크

`GET /a/{code}` → 코드 검증 → 쿠키 심고 `/` 로 303. **링크 하나 클릭 = 로그인.**

- 미들웨어 `_PUBLIC_PREFIX` 에 `/a/` 포함 (URL 자체가 자격증명이라 게이트 앞에 둔다).
  `/api/…` `/admin/…` 는 앞 3글자가 `/ap` `/ad` 라 충돌하지 않는다.
- 즉시 리다이렉트 → 주소창·북마크에 코드가 안 남는다.
  `Referrer-Policy: no-referrer`, `Cache-Control: no-store`.
- 실패 시 `/login?e=1` — 안내 문구만, 사유는 노출하지 않는다.
- **평문 코드는 발급 시점에만 존재한다.** 기존 패스의 매직 링크는 재구성 불가.
  잃어버리면 재발급하거나, 상대가 갖고 있으면 `+24h` 로 그 링크를 살린다.

트레이드오프 (감수하기로 한 것): URL 에 비밀값이 들어가 브라우저 히스토리·프록시 로그에
남을 수 있다. 읽기 전용 + 기간제 + 폐기 가능이라 받아들였다. 채팅앱 링크 프리뷰 봇이
긁으면 `use_count` 가 1 오르고 sliding 만료가 밀린다 — 무해하지만 통계는 부정확해진다.

**IP 기반 인증은 검토 후 기각**했다. CGNAT·VPN·NAT 로 오탐/미탐이 심하고, 상대 IP 를 먼저
물어봐야 해서 전달이 오히려 번거로워진다.

---

## 5. Gotchas

- **`AUTH_ENABLED` 는 필수다.** 없거나 true/false 가 아니면 `get_auth_config()` 가 import
  시점에 예외를 던져 부팅이 실패한다. 의도된 fail-fast — 무방비 공개보다 부팅 실패가 낫다.
- **docker 에서 `.env` 변경은 reload 로 안 먹는다.** `env_file` 은 컨테이너 생성 시점에
  읽히므로 watchfiles 리로드는 옛 환경 그대로 → `AuthConfigError`.
  `up -d --force-recreate` 가 필요하다.
- `/login` 의 `next` 는 미인증 입력이다 → `_safe_next()` 로 같은 사이트 절대경로만 허용
  (오픈 리다이렉트 + `</script>` 탈출 차단). 템플릿에도 스크립트가 아니라 `data-next`
  속성으로 전달한다. 이 XSS 는 실제로 검증 중에 발견됐다.
