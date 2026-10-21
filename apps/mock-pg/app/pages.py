"""결제창·영수증 HTML — 스크립트 없이 서버가 그린다 (CSP로 스크립트 전부 차단)

결제창은 쇼핑몰이 띄운 작은 창 안에서 열린다. 화면에 "가상 결제 — 실제 카드번호 입력 금지"를
늘 보여 준다(CLAUDE.md 3절 #2 예외 — 실제 카드번호 입력을 막는 장치).
"""

from html import escape

PG_NAME = "데모페이(가상 PG)"
CARD_COMPANIES = ("하늘카드", "바다카드", "숲카드", "들판카드")  # 모두 가상
INSTALLMENTS = (0, 2, 3, 6)

STYLE = """
* { box-sizing: border-box; }
body { margin: 0; font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
       background: #f4f5f7; color: #1d2433; }
main { max-width: 420px; margin: 0 auto; padding: 16px; }
.head { background: #0f766e; color: #fff; padding: 12px 16px; font-weight: 700; }
.warn { background: #fef3c7; color: #92400e; padding: 8px 16px; font-size: 14px; font-weight: 600; }
.box { background: #fff; border: 1px solid #e3e6ea; border-radius: 8px; padding: 16px;
       margin-top: 12px; }
dl { display: grid; grid-template-columns: 7em 1fr; gap: 6px 8px; margin: 0; font-size: 14px; }
dt { color: #5b6475; } dd { margin: 0; }
label { display: block; font-size: 14px; margin: 12px 0 4px; color: #5b6475; }
input, select { width: 100%; padding: 8px; font-size: 15px; border: 1px solid #c9ced6;
                border-radius: 6px; background: #fff; }
.row { display: flex; gap: 8px; margin-top: 16px; }
button { flex: 1; padding: 10px; font-size: 15px; border-radius: 6px; border: 0; cursor: pointer; }
.primary { background: #0f766e; color: #fff; font-weight: 700; }
.secondary { background: #e3e6ea; color: #1d2433; }
.error { background: #fee2e2; color: #991b1b; padding: 8px 12px; border-radius: 6px;
         font-size: 14px; }
.muted { color: #5b6475; font-size: 13px; }
"""


def _won(amount: int) -> str:
    return f"{amount:,}원"


def _layout(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title><style>{STYLE}</style></head>
<body><div class="head">{escape(PG_NAME)}</div>
<div class="warn" role="alert">가상 결제 — 실제 카드번호 입력 금지</div>
<main>{body}</main></body></html>"""


def checkout_page(payment: dict, error: str | None = None) -> str:
    key = escape(payment["payment_key"])
    cards = "".join(f"<option>{escape(c)}</option>" for c in CARD_COMPANIES)
    plans = "".join(
        f'<option value="{m}">{"일시불" if m == 0 else f"{m}개월"}</option>' for m in INSTALLMENTS
    )
    alert = f'<p class="error" role="alert">{escape(error)}</p>' if error else ""
    return _layout(
        "결제",
        f"""
<div class="box"><dl>
  <dt>주문</dt><dd>{escape(payment["order_name"])}</dd>
  <dt>결제 금액</dt><dd><strong>{_won(payment["amount"])}</strong></dd>
</dl></div>
<div class="box">{alert}
<form method="post" action="/checkout/{key}/approve" autocomplete="off">
  <label for="card_company">카드사</label>
  <select id="card_company" name="card_company">{cards}</select>
  <label for="card_number">카드번호</label>
  <input id="card_number" name="card_number" inputmode="numeric" maxlength="23" required>
  <label for="expiry">유효기간 (MM/YY)</label>
  <input id="expiry" name="expiry" inputmode="numeric" maxlength="5">
  <label for="installment">할부</label>
  <select id="installment" name="installment">{plans}</select>
  <div class="row">
    <button class="primary" type="submit">{_won(payment["amount"])} 결제</button>
  </div>
</form>
<form method="post" action="/checkout/{key}/abort">
  <div class="row"><button class="secondary" type="submit">결제 취소</button></div>
</form>
<p class="muted">카드번호는 이 결제창(데모페이)만 받고, 쇼핑몰에는 마스킹한 번호만 전달됩니다.</p>
</div>""",
    )


def error_page(message: str) -> str:
    return _layout("결제 오류", f'<div class="box"><p class="error">{escape(message)}</p></div>')


def receipt_page(payment: dict) -> str:
    status = {"DONE": "승인", "CANCELED": "취소"}.get(payment["status"], payment["status"])
    approved = (
        payment["approved_at"].isoformat(timespec="seconds") if payment["approved_at"] else "-"
    )
    months = payment["installment_months"] or 0
    return _layout(
        "영수증",
        f"""
<div class="box"><h1 style="font-size:18px;margin:0 0 12px">카드 영수증</h1><dl>
  <dt>상태</dt><dd>{escape(status)}</dd>
  <dt>주문</dt><dd>{escape(payment["order_name"])}</dd>
  <dt>금액</dt><dd>{_won(payment["amount"])}</dd>
  <dt>카드</dt><dd>{escape(payment["card_company"] or "-")}
      {escape(payment["card_number_masked"] or "")}</dd>
  <dt>할부</dt><dd>{"일시불" if months == 0 else f"{months}개월"}</dd>
  <dt>승인번호</dt><dd>{escape(payment["approve_no"] or "-")}</dd>
  <dt>승인 시각</dt><dd>{escape(approved)}</dd>
</dl></div>""",
    )
