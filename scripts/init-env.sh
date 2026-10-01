#!/usr/bin/env bash
# 로컬 실행용 .env 만들기 — 레포 루트에서 Git Bash로:  bash scripts/init-env.sh
#
# .env.example을 복사하면서 change-me 칸(DB 비밀번호·서명 키 등)만 무작위 값(openssl rand -hex 32)으로 채운다.
# 값은 화면에 출력하지 않는다. 이미 .env가 있으면 덮어쓰지 않는다 — 기존 DB 비밀번호와 어긋나지 않게.
# CI의 e2e job도 이 스크립트로 일회용 .env를 만든다.
set -euo pipefail

cd "$(dirname "$0")/.."
if [[ -e .env ]]; then
  echo "이미 .env가 있습니다 — 덮어쓰지 않습니다." >&2
  echo "새로 만들려면: docker compose down -v (DB 삭제) → rm .env → 다시 실행" >&2
  exit 1
fi

umask 077 # 만든 사람만 읽을 수 있게 (Linux·macOS)
while IFS= read -r line; do
  if [[ "$line" =~ ^([A-Z0-9_]+)=change-me ]]; then
    echo "${BASH_REMATCH[1]}=$(openssl rand -hex 32)"
  else
    echo "$line"
  fi
done < .env.example > .env

echo ".env를 만들었습니다 (무작위 값은 출력하지 않음)."
