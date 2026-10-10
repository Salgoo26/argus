// Argus 웹 푸시 서비스 워커 (v0.1 보강 F-4)
// Argus 탭이 닫혀 있어도 브라우저가 켜져 있으면 받는다. 푸시 내용은 서버가 정한 짧은 문구와
// 탐지건 화면 주소뿐 — 회원번호·취급자 이름·룰 상세는 없다(브라우저 제조사 푸시 서버를 거치므로).
// 클릭하면 Argus를 열고(로그인이 필요하면 로그인 화면을 거쳐) 그 탐지건으로 간다.

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = {};
  }
  // 같은 주소(이 사이트) 안의 경로만 연다 — 푸시 내용이 바깥 주소로 데려가지 못하게
  const url = typeof data.url === "string" && data.url.startsWith("/") && !data.url.startsWith("//") ? data.url : "/detections";
  event.waitUntil(
    self.registration.showNotification(data.title || "Argus", {
      body: data.body || "새 알림이 있습니다",
      tag: data.tag,
      data: { url },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = new URL(event.notification.data?.url || "/detections", self.location.origin).href;
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
      for (const client of windows) {
        if (client.url.startsWith(self.location.origin) && "navigate" in client) {
          return client.focus().then(() => client.navigate(url));
        }
      }
      return self.clients.openWindow(url);
    }),
  );
});
