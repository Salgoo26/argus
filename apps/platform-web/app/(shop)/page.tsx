import Link from "next/link";

export default function ShopHome() {
  return (
    <section className="card hero">
      <h1 className="page-title">데모 커머스</h1>
      <p className="page-subtitle">
        Argus(접속기록 점검 시스템)가 감시하는 가상의 쇼핑몰입니다. 고객 화면은 개인정보 수집·동의·
        열람·정정·탈퇴 흐름을 보여 주기 위한 최소 구현이며, 고객의 행위는 접속기록 대상이 아닙니다.
      </p>
      <div className="toolbar">
        <Link href="/signup" className="btn btn-primary">
          회원가입
        </Link>
        <Link href="/privacy" className="btn btn-secondary">
          개인정보 처리방침
        </Link>
      </div>
    </section>
  );
}
