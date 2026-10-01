import type { NextConfig } from "next";

// 브라우저는 이 화면 서버하고만 통신하고, /api/* 요청은 Next.js가 argus-api로 대신 전달한다(rewrite).
// - 화면과 API가 같은 출처라 SameSite=Strict·HttpOnly 세션 쿠키가 그대로 동작하고 CORS를 열 필요가 없다
// - 운영에서는 Caddy가 같은 역할(argus.<도메인>/api → argus-api, architecture 7-2)
// - argus-api의 화면용 API는 경로가 /api/... 그대로다 (/ingest는 화면에서 부르지 않는다)
// - 이 화면 서버는 argus-api의 신뢰 프록시가 아니다: Next.js rewrite는 원래 요청자 IP를 붙이지 않으면서
//   클라이언트가 보낸 X-Forwarded-For를 그대로 전달하므로, 믿으면 접속지 위조가 가능해진다 (M5 PR ① 발견)
const ARGUS_API_URL = process.env.ARGUS_API_URL ?? "http://argus-api:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${ARGUS_API_URL}/api/:path*` }];
  },
};

export default nextConfig;
