#!/usr/bin/env bash
# Walking Skeleton E2E 시나리오 테스트 (M6) — 레포 루트에서 Git Bash로 실행:
#
#   bash scripts/e2e.sh
#
# 1) 전체 스택을 별도 프로젝트(argus-e2e)로 새로 띄운다 — 평소 개발 스택(argus)과 데이터·포트가 섞이지 않는다
# 2) e2e 컨테이너에서 시나리오(pytest)를 실행한다 — e2e/test_scenario.py
# 3) 결과와 관계없이 argus-e2e 스택을 볼륨까지 지운다 (E2E_KEEP=1이면 남겨 두고 직접 살펴볼 수 있다)
#
# 환경변수 파일은 레포 루트의 .env를 쓴다 (CI는 실행 직전에 무작위 값으로 만든다). 다른 파일: E2E_ENV_FILE=경로
set -euo pipefail

cd "$(dirname "$0")/.."
ENV_FILE="${E2E_ENV_FILE:-.env}"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "환경변수 파일이 없습니다: $ENV_FILE (cp .env.example .env 후 값 채우기 — README 참고)" >&2
  exit 2
fi

dc() {
  docker compose --env-file "$ENV_FILE" -p argus-e2e \
    -f infra/docker-compose.yml -f infra/docker-compose.e2e.yml "$@"
}

cleanup() {
  local status=$?
  if [[ $status -ne 0 ]]; then
    echo "── 실패 — 컨테이너 로그(마지막 200줄) ──" >&2
    dc logs --no-color --tail 200 >&2 || true
  fi
  if [[ "${E2E_KEEP:-0}" == "1" ]]; then
    echo "E2E_KEEP=1 — 스택을 남겨 둠. 정리: docker compose -p argus-e2e down -v" >&2
  else
    dc --profile e2e down -v --remove-orphans >/dev/null 2>&1 || true
  fi
  exit "$status"
}
trap cleanup EXIT

echo "── 1/2 스택 기동 (argus-e2e, 이미지 빌드 포함) ──"
dc --profile e2e down -v --remove-orphans >/dev/null 2>&1 || true # 지난 실행의 잔여물 제거
# --wait는 쓰지 않는다: relay·worker는 웹 서버가 없어 healthcheck를 꺼 두었고, Compose 버전에 따라
# "no healthcheck configured"로 실패한다(CI에서 확인). 화면 서버가 healthy해질 때까지의 대기는
# e2e 서비스의 depends_on이 맡는다
dc up -d --build

echo "── 2/2 시나리오 실행 ──"
dc --profile e2e run --rm --build e2e
echo "E2E 통과"
