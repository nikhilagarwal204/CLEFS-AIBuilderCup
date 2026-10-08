import MenuConsole from "./MenuConsole";

/* Server entry for the menu console. `?tab=` is read here and handed down
   as a prop, so the client console needs no useSearchParams (which would
   force a Suspense boundary and a client-side bailout for the page). */
export default async function MenuPage({
  searchParams,
}: {
  searchParams: Promise<{ tab?: string | string[] }>;
}) {
  const sp = await searchParams;
  return <MenuConsole initialTab={typeof sp.tab === "string" ? sp.tab : undefined} />;
}
