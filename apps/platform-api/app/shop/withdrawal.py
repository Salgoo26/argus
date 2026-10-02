"""회원 탈퇴 = 즉시 파기 (기능 레이어 7 결정 6, PIPA §21①)

탈퇴 트랜잭션 안에서 회원 행과 그에 딸린 개인정보를 **실제 삭제**한다(논리 삭제 아님).
db-schema 4-1은 "상태 변경 → 파기 배치"였지만 파기 배치는 v0.2라, 그대로면 v0.1 내내 탈퇴 회원
정보가 남는다 → 탈퇴 시점에 바로 지운다(설계 변경).

삭제 순서는 FK 방향을 따른다 — 회원을 참조하는 행부터.
"""

from sqlalchemy import Connection, delete

from app.models import member, member_consent


def destroy_member(conn: Connection, member_id: int) -> None:
    # 동의 이력도 함께 파기 — 탈퇴로 동의 자체가 실효된다 (db-schema 4-1 "동의 이력" 주석)
    conn.execute(delete(member_consent).where(member_consent.c.member_id == member_id))
    conn.execute(delete(member).where(member.c.id == member_id))
