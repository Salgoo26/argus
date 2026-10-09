"""웹 푸시 VAPID 키 만들기 (v0.1 보강 F-4) — python -m app.scripts.vapid_keys

출력한 두 줄을 .env에 붙여 넣고 argus-api·argus-worker를 다시 띄운다.
개인키는 시크릿이다 — 레포·채팅·이슈에 붙이지 않는다 (CLAUDE.md 3절 #1). 키를 바꾸면 브라우저마다
"알림 받기"를 다시 눌러야 한다(기존 구독은 옛 공개키에 묶여 있음).

실행 예 (Git Bash, 레포 루트):
    docker compose run --rm --no-deps argus-api python -m app.scripts.vapid_keys >> .env
"""

from app.notifications.webpush import generate_vapid


def main() -> None:
    public_key, private_key = generate_vapid()
    print(f"ARGUS_VAPID_PUBLIC_KEY={public_key}")
    print(f"ARGUS_VAPID_PRIVATE_KEY={private_key}")


if __name__ == "__main__":
    main()
