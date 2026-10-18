import ConfigConsole from "./ConfigConsole";

/* /app/[hotelSlug]/service/config. `?tab=` is read on the server and handed
   down, so the client needs no useSearchParams Suspense boundary. */
export default async function Page({ searchParams }: { searchParams: Promise<{ tab?: string | string[] }> }) {
  const sp = await searchParams;
  return <ConfigConsole initialTab={typeof sp.tab === "string" ? sp.tab : undefined} />;
}
