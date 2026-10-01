import type { NextConfig } from "next";

// 브라우저는 이 화면 서버하고만 통신하고, /api/* 요청은 Next.js가 platform-api로 대신 전달한다(rewrite).
// - 화면과 API가 같은 출처라 SameSite=Strict·HttpOnly 세션 쿠키가 그대로 동작하고 CORS를 열 필요가 없다
// - 운영에서는 Caddy가 같은 역할(shop.<도메인>/api → platform-api, architecture 7-2)
// - 목적지는 빌드 시점에 고정된다 (compose 내부 서비스 이름)
const PLATFORM_API_URL = process.env.PLATFORM_API_URL ?? "http://platform-api:8000";

const nextConfig: NextConfig = {
  output: "standalone", // 실행에 필요한 파일만 모아 작은 런타임 이미지를 만든다
  poweredByHeader: false, // 응답 헤더로 프레임워크를 광고하지 않는다
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${PLATFORM_API_URL}/:path*` }];
  },
};

export default nextConfig;
