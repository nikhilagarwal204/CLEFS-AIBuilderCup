"use client";

import { LuArrowLeft } from "react-icons/lu";
import { useApi } from "../../../../../../lib/api";
import { useHotel } from "../../../../../../lib/hotel";
import { Button, Card } from "../../../../../../ui";
import ActionDetail from "../../ActionDetail";
import { brfApi, brfHref } from "../../lib";
import type { WaContact } from "../../types";

/* The standalone action page: the same ActionDetail the drawer shows, with
   member names for the assignee line and a way back to the list. */
export default function ActionPage({ actionId }: { actionId: number }) {
  const { slug } = useHotel();
  const contacts = useApi<WaContact[]>(brfApi(slug, "/wa/contacts"), [slug]);
  const names = new Map((contacts.data ?? []).map(c => [c.user_id, c.name] as const));
  return (
    <>
      <div className="brf-row">
        <Button size="sm" variant="ghost" icon={<LuArrowLeft />} href={brfHref(slug, "/actions")}>All actions</Button>
      </div>
      <Card>
        <ActionDetail slug={slug} actionId={actionId} names={names} />
      </Card>
    </>
  );
}
