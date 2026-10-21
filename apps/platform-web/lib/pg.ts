// 가상 PG(데모페이) 결제창 연동 — 주문 화면과 결과 페이지가 함께 쓴다 (커머스 확장 F6)

// PG 결제창(작은 창)이 결제를 마치고 돌아오는 쇼핑몰 주소 — 그 페이지가 결과를 주문 화면으로 넘겨준다
export const PG_RESULT_PATH = "/checkout/result";
export const PG_WINDOW = "demopay";

export type PgResult =
  | { type: "pg-result"; status: "success"; orderId: number; paymentKey: string; amount: number }
  | { type: "pg-result"; status: "fail"; orderId: number; code: string; message: string };
