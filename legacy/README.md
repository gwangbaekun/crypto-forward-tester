# legacy/

폐기했지만 보존하는 자산. 나중에 재활용/포트폴리오용.

## gcp/
Polymarket GCE(GCP VM) 자동배포 세트 — **아카이브됨 (2026-06-24)**.
GitHub Actions 워크플로(`deploy-polymarket-vm.yml.disabled`)를 `.github/workflows/`에서 빼서 비활성화.
GCP 빌링 off로 인스턴스 이미 정지. 나중에 다른 클라우드에서 재활성화 시 참고.
- `deploy-polymarket-vm.yml.disabled` — GitHub Actions 배포 워크플로
- `polymarket-worker.service` — systemd 유닛
- `setup_gce_polymarket.sh`, `vm_install_polymarket.sh`, `vm_bootstrap_github.sh`, `vm_deploy.sh` — VM 부트스트랩/배포 스크립트

## Polymarket 전략 자체
코드는 `src/features/strategy/polymarket/` 에 dormant 보존 (전부 enabled:false).
폐기 사유·결론: `src/features/strategy/polymarket/LEGACY.md`.
