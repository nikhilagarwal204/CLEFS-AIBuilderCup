import ProfilesView from "./ProfilesView";

/* Profiles list. The server reads ?id= (open one profile, linked from the
   badges) and ?returning= / ?vip= (linked from the overview tiles) and hands
   them down as props, so the client view needs no useSearchParams (which
   would force a Suspense bailout for the whole page). */
export default async function ProfilesPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const sp = await searchParams;
  const one = (k: string) => (Array.isArray(sp[k]) ? sp[k]?.[0] : sp[k]) as string | undefined;
  const id = Number(one("id"));
  const filter = one("vip") === "1" ? "vip" : one("returning") === "1" ? "returning" : "all";
  return <ProfilesView initialId={Number.isFinite(id) && id > 0 ? id : null} initialFilter={filter} />;
}
