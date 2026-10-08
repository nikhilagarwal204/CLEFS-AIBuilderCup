"use client";

/* /device/ is the installed PWA's start_url (one manifest serves every
   hotel, so it cannot name one). It forwards to the hotel this device was
   paired to, remembered by DeviceApp; a device never paired sees how to
   start. */

import { useEffect, useSyncExternalStore } from "react";
import { useRouter } from "next/navigation";
import { UiStyles } from "../ui";
import { deviceStyles } from "./lib/styles";
import { LAST_HOTEL_KEY } from "./lib/lastHotel";

const noSubscribe = () => () => {};

function readHotel(): string | null {
  try {
    const slug = window.localStorage.getItem(LAST_HOTEL_KEY);
    return slug && /^[a-z0-9-]{1,80}$/i.test(slug) ? slug : null;
  } catch {
    return null;
  }
}

export default function DeviceIndex() {
  const router = useRouter();
  // undefined on the server, null on a device never paired.
  const slug = useSyncExternalStore(noSubscribe, readHotel, () => undefined);
  const unknown = slug === null;

  useEffect(() => {
    if (slug) router.replace(`/device/${encodeURIComponent(slug)}`);
  }, [slug, router]);

  return (
    <div className="svc-dv">
      <UiStyles />
      <style dangerouslySetInnerHTML={{ __html: deviceStyles }} />
      <div className="svc-dv-center">
        <div className="svc-dv-brand">Clefs floor device</div>
        {unknown ? (
          <>
            <h1 className="svc-dv-h1">Not paired yet</h1>
            <p className="svc-dv-lead">
              Scan the pairing QR code on your manager&apos;s screen (Service, Staff, &quot;Pair device&quot;), or open the link they send you.
            </p>
          </>
        ) : (
          <p className="svc-dv-lead">Opening your device...</p>
        )}
      </div>
    </div>
  );
}
