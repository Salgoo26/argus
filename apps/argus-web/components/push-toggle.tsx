"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";

// 웹 푸시 "알림 받기" (v0.1 보강 F-4) — 한 번 허락하면 Argus 탭이 닫혀 있어도 브라우저가 켜져 있으면
// 급한 알림(상 탐지건·상 소명 요청, 상·중 기한 임박·초과)을 받는다. 로그아웃하면 서버는 구독을 지우고
// 화면은 브라우저 구독도 해제한다 — 다음 사람은 "알림 받기"를 직접 다시 눌러야 한다.
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

function supported(): boolean {
  return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

async function currentSubscription(): Promise<PushSubscription | null> {
  const registration = await navigator.serviceWorker.getRegistration("/sw.js");
  return registration ? registration.pushManager.getSubscription() : null;
}

// 브라우저 구독이 지금 서버 키로 만든 것인가 — 서버가 키를 바꾸면 옛 구독으로는 받을 수 없다
function sameKey(subscription: PushSubscription, publicKey: string): boolean {
  const current = subscription.options.applicationServerKey;
  if (!current) return true;
  const a = new Uint8Array(current);
  const b = keyBytes(publicKey);
  return a.length === b.length && a.every((v, i) => v === b[i]);
}

function register(subscription: PushSubscription): Promise<unknown> {
  const json = subscription.toJSON();
  return api("/push/subscriptions", {
    method: "POST",
    body: JSON.stringify({ endpoint: json.endpoint, keys: json.keys }),
  });
}

/**
 * 지금 상태를 판정한다. sync면 브라우저에 남은 구독을 서버와 맞춘다 — 지금 로그인한 사람의 것으로
 * 다시 저장(같은 주소면 덮어씀). 로그아웃 버튼을 거치지 않고 세션이 끝난 뒤(30분 미사용) 다시
 * 로그인했을 때 "받는 중"으로 보이는데 서버에는 구독이 없는 어긋남을 막는다 (2026-10-10 실측에서 발견)
 */
async function pushState(sync: boolean): Promise<{ state: State; config: PushConfig | null }> {
  if (!supported()) return { state: "unsupported", config: null };
  const config = await api<PushConfig>("/push/config").catch(() => null);
  if (!config?.enabled || !config.public_key) return { state: "disabled", config };
  if (Notification.permission === "denied") return { state: "denied", config };
  const subscription = await currentSubscription();
  if (!subscription || Notification.permission !== "granted") return { state: "off", config };
  if (!sameKey(subscription, config.public_key)) {
    await subscription.unsubscribe().catch(() => undefined); // 키가 바뀜 — 다시 "알림 받기"
    return { state: "off", config };
  }
  if (sync) {
    try {
      await register(subscription);
    } catch {
      return { state: "off", config };
    }
  }
  return { state: "on", config };
}

// 이 탭에서 마지막으로 맞춘 계정 — 화면을 옮길 때마다 다시 보내지 않게
let syncedFor: string | null = null;

/** 로그인한 계정이 정해지면 한 번 — 브라우저 구독을 서버와 맞춘다 (헤더가 부른다) */
export function syncBrowserPush(loginId: string): void {
  if (syncedFor === loginId) return;
  syncedFor = loginId;
  pushState(true).catch(() => undefined);
}

/**
 * 로그아웃 때 이 브라우저의 구독도 해제한다 — 서버는 로그아웃 요청에서 구독을 지우지만 브라우저
 * 쪽이 남아 있으면 다음 사람에게 "받는 중"으로 보인다. 다음 사람은 "알림 받기"를 직접 눌러야 한다
 */
export async function releaseBrowserPush(): Promise<void> {
  syncedFor = null;
  if (!supported()) return;
  const subscription = await currentSubscription().catch(() => null);
  await subscription?.unsubscribe().catch(() => undefined);
}

export function PushToggle() {
  const [state, setState] = useState<State>("loading");
  const [config, setConfig] = useState<PushConfig | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    pushState(false).then((result) => {
      setConfig(result.config);
      setState(result.state);
    });
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
      await register(subscription);
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
          <span>
            웹 푸시 받는 중 — 급한 알림만(상 탐지건·상 소명 요청, 상·중 기한 임박·초과). 로그아웃하면
            꺼집니다
          </span>
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
