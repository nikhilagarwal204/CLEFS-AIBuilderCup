import { notFound } from "next/navigation";
import WeeklyReportView from "../../WeeklyReportView";

/* /brief/reports/weekly/{2026-W40}: the path weekly.summary() puts in the
   weekly brief's report block. A malformed key is a 404 here, before any
   request; a week that has not ended is the server's 409, shown in words. */
export default async function BriefWeeklyReportPage({ params }: { params: Promise<{ week: string }> }) {
  const { week } = await params;
  const key = decodeURIComponent(week).toUpperCase();
  if (!/^\d{4}-W\d{2}$/.test(key)) notFound();
  return <WeeklyReportView week={key} />;
}
