"""가상 택배 자체 DB — 쇼핑몰(플랫폼) DB와 완전히 분리된 별도 인스턴스

스키마는 기동할 때 만든다(없을 때만). 작은 외부 서비스 흉내라 마이그레이션 도구를 두지 않는다.

배송 상태:
    RECEIVED(접수) → PICKED_UP(집하) → IN_TRANSIT(간선 이동) → OUT_FOR_DELIVERY(배송 출발)
    → DELIVERED(배송 완료).  집하 전에만 → CANCELED(접수 취소)
반품 회수 상태:
    RETURN_RECEIVED(회수 접수) → RETURN_PICKED_UP(회수 집하) → RETURN_IN_TRANSIT(회수 이동)
    → RETURN_DELIVERED(판매자 도착).  집하 전에만 → CANCELED

상태는 setting 표의 시간이 지나면 서비스가 한 단계씩 넘긴다(auto_progress).
DB에서 status를 직접 바꿔도 트리거가 변경 시각·송장번호·이력·통지 대기를 맞추므로,
다음 루프에서 쇼핑몰로 통지된다.
"""

from sqlalchemy import Engine, text

DELIVERY_FLOW = ("RECEIVED", "PICKED_UP", "IN_TRANSIT", "OUT_FOR_DELIVERY", "DELIVERED")
RETURN_FLOW = ("RETURN_RECEIVED", "RETURN_PICKED_UP", "RETURN_IN_TRANSIT", "RETURN_DELIVERED")
NEXT_STATUS = {
    a: b for flow in (DELIVERY_FLOW, RETURN_FLOW) for a, b in zip(flow, flow[1:], strict=False)
}
STATUS_LABELS = {
    "RECEIVED": "접수",
    "PICKED_UP": "집하",
    "IN_TRANSIT": "간선 이동",
    "OUT_FOR_DELIVERY": "배송 출발",
    "DELIVERED": "배송 완료",
    "RETURN_RECEIVED": "회수 접수",
    "RETURN_PICKED_UP": "회수 집하",
    "RETURN_IN_TRANSIT": "회수 이동",
    "RETURN_DELIVERED": "판매자 도착",
    "CANCELED": "접수 취소",
}
# 기본 진행 시간(초) — 각 상태에 머무는 시간. DB의 setting 표에서 바꾸면 다음 루프부터 적용
DEFAULT_SETTINGS = {
    "auto_progress": ("on", "on이면 아래 시간에 따라 상태를 자동으로 넘긴다 (off: DB 직접 변경만)"),
    "delay.RECEIVED": ("60", "접수 → 집하(송장번호 발급)까지 초"),
    "delay.PICKED_UP": ("30", "집하 → 간선 이동까지 초"),
    "delay.IN_TRANSIT": ("30", "간선 이동 → 배송 출발까지 초"),
    "delay.OUT_FOR_DELIVERY": ("30", "배송 출발 → 배송 완료까지 초"),
    "delay.RETURN_RECEIVED": ("60", "회수 접수 → 회수 집하까지 초"),
    "delay.RETURN_PICKED_UP": ("30", "회수 집하 → 회수 이동까지 초"),
    "delay.RETURN_IN_TRANSIT": ("30", "회수 이동 → 판매자 도착까지 초"),
}


def _quoted(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


# 끼워 넣는 값은 코드 상수(상태 이름)뿐
SCHEMA = f"""
CREATE SEQUENCE IF NOT EXISTS tracking_seq START 100000001;

CREATE TABLE IF NOT EXISTS shipment (
    shipment_id             varchar(32)  PRIMARY KEY,
    kind                    varchar(8)   NOT NULL CHECK (kind IN ('DELIVERY', 'RETURN')),
    client_order_no         varchar(64)  NOT NULL,
    -- 보내는 곳 (배송: 쇼핑몰 / 반품: 고객 — 회수지)
    sender_name             varchar(50)  NOT NULL,
    sender_phone            varchar(20)  NOT NULL,
    sender_zip_code         varchar(10)  NOT NULL,
    sender_address          varchar(255) NOT NULL,
    sender_address_detail   varchar(100),
    -- 받는 곳 (배송: 고객 / 반품: 쇼핑몰)
    receiver_name           varchar(50)  NOT NULL,
    receiver_phone          varchar(20)  NOT NULL,
    receiver_zip_code       varchar(10)  NOT NULL,
    receiver_address        varchar(255) NOT NULL,
    receiver_address_detail varchar(100),
    memo                    varchar(100),
    items                   jsonb        NOT NULL,
    original_tracking_no    varchar(20),
    tracking_no             varchar(20)  UNIQUE,
    status                  varchar(20)  NOT NULL,
    status_changed_at       timestamptz  NOT NULL DEFAULT now(),
    created_at              timestamptz  NOT NULL DEFAULT now(),
    picked_up_at            timestamptz,
    delivered_at            timestamptz,
    canceled_at             timestamptz,
    -- 쇼핑몰 통지: 마지막으로 통지에 성공한 상태와 재시도 일정
    notified_status         varchar(20),
    notify_attempts         integer      NOT NULL DEFAULT 0,
    next_notify_at          timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT shipment_status_check CHECK (
        (kind = 'DELIVERY' AND status IN ({_quoted(DELIVERY_FLOW)}, 'CANCELED'))
        OR (kind = 'RETURN' AND status IN ({_quoted(RETURN_FLOW)}, 'CANCELED'))
    ),
    -- 같은 주문을 두 번 접수하지 않는다 (재요청이면 기존 접수를 돌려준다)
    CONSTRAINT shipment_order_unique UNIQUE (kind, client_order_no)
);

CREATE TABLE IF NOT EXISTS shipment_event (
    id          bigserial   PRIMARY KEY,
    shipment_id varchar(32) NOT NULL REFERENCES shipment (shipment_id),
    status      varchar(20) NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    -- 누가 바꿨나: API(접수·취소), AUTO(시간 경과), DB(직접 변경 — 앱이 표시하지 않은 변경)
    source      varchar(8)  NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_shipment_event_shipment ON shipment_event (shipment_id);

CREATE TABLE IF NOT EXISTS setting (
    key         varchar(40)  PRIMARY KEY,
    value       varchar(40)  NOT NULL,
    description varchar(200)
);

-- 상태가 바뀌면(앱이든 DB 직접 변경이든) 변경 시각·송장번호·시각 칸·통지 대기를 맞춘다
CREATE OR REPLACE FUNCTION shipment_status_changed() RETURNS trigger AS $$
BEGIN
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        NEW.status_changed_at := now();
        IF NEW.status NOT IN ('RECEIVED', 'RETURN_RECEIVED', 'CANCELED')
           AND NEW.tracking_no IS NULL THEN
            NEW.tracking_no := '6' || lpad(nextval('tracking_seq')::text, 11, '0');
        END IF;
        IF NEW.status NOT IN ('RECEIVED', 'RETURN_RECEIVED', 'CANCELED')
           AND NEW.picked_up_at IS NULL THEN
            NEW.picked_up_at := now();
        END IF;
        IF NEW.status IN ('DELIVERED', 'RETURN_DELIVERED') AND NEW.delivered_at IS NULL THEN
            NEW.delivered_at := now();
        END IF;
        IF NEW.status = 'CANCELED' AND NEW.canceled_at IS NULL THEN
            NEW.canceled_at := now();
        END IF;
        NEW.notify_attempts := 0;
        NEW.next_notify_at := now();
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION shipment_record_event() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'INSERT' OR NEW.status IS DISTINCT FROM OLD.status THEN
        INSERT INTO shipment_event (shipment_id, status, source)
        VALUES (NEW.shipment_id, NEW.status,
                coalesce(nullif(current_setting('courier.source', true), ''), 'DB'));
    END IF;
    RETURN NULL;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS shipment_status_changed ON shipment;
CREATE TRIGGER shipment_status_changed BEFORE UPDATE ON shipment
    FOR EACH ROW EXECUTE FUNCTION shipment_status_changed();
DROP TRIGGER IF EXISTS shipment_record_event ON shipment;
CREATE TRIGGER shipment_record_event AFTER INSERT OR UPDATE ON shipment
    FOR EACH ROW EXECUTE FUNCTION shipment_record_event();
"""  # noqa: S608


def create_schema(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('mock-courier-schema'))"))
        conn.exec_driver_sql(SCHEMA)
        for key, (value, description) in DEFAULT_SETTINGS.items():
            conn.execute(
                text(
                    "INSERT INTO setting (key, value, description) VALUES (:k, :v, :d)"
                    " ON CONFLICT (key) DO NOTHING"
                ),
                {"k": key, "v": value, "d": description},
            )


def mark_source(conn, source: str) -> None:
    """이 트랜잭션의 상태 변경을 누가 했는지 이력에 남긴다 (표시하지 않은 변경 = DB 직접 변경)"""
    conn.execute(text("SELECT set_config('courier.source', :s, true)"), {"s": source})
