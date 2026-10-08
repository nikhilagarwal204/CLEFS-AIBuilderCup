import StaffDashboard from "../../../StaffDashboard";

/* The Service module IS the existing staff dashboard, mounted unchanged
   (full UI kept, Canon item 14). Its own sidebar is the Service sub-nav;
   ConsoleShell only moves that sidebar below the top bar. */
export default async function ServicePage({ params }: { params: Promise<{ hotelSlug: string }> }) {
  const { hotelSlug } = await params;
  return <StaffDashboard hotelSlug={hotelSlug} />;
}
