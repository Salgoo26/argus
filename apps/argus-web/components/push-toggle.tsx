"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";

// 웹 푸시 "알림 받기" (v0.1 보강 F-4) — 한 번 허락하면 Argus 탭이 닫혀 있어도 브라우저가 켜져 있으면
// 급한 알림(상 탐지건·소명 요청, 기한 임박·초과)을 받는다. 로그아웃하면 서버가 구독을 지운다.
// 브라우저는 http://localhost를 예외로 허용하고, 운영은 HTTPS가 전제다.
type PushConfig = { enabled: boolean; public_key: string | null };
type State = "loading" | "unsupported" | "disabled" | "denied" | "off" | "on";

function keyBytes(base64url: string): Uint8Array<ArrayBuffer> {
  const padded = (base64url + "=".repeat((4 - (base64url.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(padded);
  const bytes = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
  return bytes;
}

async function currentSubscription(): Promise<PushSubscription | null> {
  const registration = await navigator.serviceWorker.getRegistration("/sw.js");
  return registration ? registration.pushManager.getSubscription() : null;
}

export function PushToggle() {
  const [state, setState] = useState<State>("loading");
  const [config, setConfig] = useState<PushConfig | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
      // 지원 여부 판정도 비동기 흐름 안에서 (렌더 중 상태 갱신을 피함)
      Promise.resolve().then(() => setState("unsupported"));
      return;
    }
    api<PushConfig>("/push/config")
      .then(async (c) => {
        setConfig(c);
        if (!c.enabled) return setState("disabled");
        if (Notification.permission === "denied") return setState("denied");
        setState((await currentSubscription()) ? "on" : "off");
      })
      .catch(() => setState("disabled"));
  }, []);

  async function enable() {
    if (!config?.public_key) return;
    setBusy(true);
    try {
      if ((await Notification.requestPermission()) !== "granted") {
        setState("denied");
        return;
      }
      const registration = await navigator.serviceWorker.register("/sw.js");
      await navigator.serviceWorker.ready;
      const subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: keyBytes(config.public_key),
      });
      const json = subscription.toJSON();
      await api("/push/subscriptions", {
        method: "POST",
        body: JSON.stringify({ endpoint: json.endpoint, keys: json.keys }),
      });
      setState("on");
    } catch {
      setState("off");
    } finally {
      setBusy(false);
    }
  }

  async function disable() {
    setBusy(true);
    try {
      const subscription = await currentSubscription();
      if (subscription) {
        await api("/push/unsubscribe", {
          method: "POST",
          body: JSON.stringify({ endpoint: subscription.endpoint }),
        }).catch(() => undefined);
        await subscription.unsubscribe();
      }
      setState("off");
    } finally {
      setBusy(false);
    }
  }

  if (state === "loading") return null;
  return (
    <div className="push-toggle">
      {state === "on" && (
        <>
          <span>웹 푸시 받는 중 (급한 알림만)</span>
          <button className="link-button" onClick={disable} disabled={busy}>
            끄기
          </button>
        </>
      )}
      {state === "off" && (
        <>
          <span className="muted">Argus를 닫아도 급한 알림을 받으려면</span>
          <button className="link-button" onClick={enable} disabled={busy}>
            알림 받기
          </button>
        </>
      )}
      {state === "denied" && <span className="muted">브라우저에서 알림이 차단되어 있습니다.</span>}
      {state === "disabled" && <span className="muted">이 서버는 웹 푸시를 쓰지 않습니다(화면 알림만).</span>}
      {state === "unsupported" && <span className="muted">이 브라우저는 웹 푸시를 지원하지 않습니다.</span>}
    </div>
  );
}
