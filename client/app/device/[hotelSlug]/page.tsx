import { DeviceRoot } from "../components/DeviceApp";

/* /device/{slug}: the floor-device PWA for one hotel (03 spec 1.8). A server
   shell that reads the async params (Next 16) and hands them to the client
   app. ?code= (the foundation's pair_url) or ?c= (the spec's QR form)
   carries a pairing code; the client strips it from the address bar. */

type Params = Promise<{ hotelSlug: string }>;
type Search = Promise<Record<string, string | string[] | undefined>>;

const first = (v: string | string[] | undefined): string | null =>
  (Array.isArray(v) ? v[0] : v)?.trim() || null;

export default async function DevicePage({ params, searchParams }: { params: Params; searchParams: Search }) {
  const { hotelSlug } = await params;
  const sp = await searchParams;
  const code = first(sp.code) ?? first(sp.c);
  return <DeviceRoot hotelSlug={decodeURIComponent(hotelSlug)} pairCode={code} />;
}
