import NewInquiry from "./NewInquiry";

/* /inquiries/new: log an inquiry taken on the phone or at the desk
   (source phone_desk). The caller already spoke to a person, so the server
   records the first response at creation and runs no reply clock.
   Service's phone-entry console links here with ?source=phone_desk&room=
   when an in-house caller asks about a booking (04 spec 4.3); the room is
   read here, on the server, so the client form needs no Suspense boundary. */
type Props = { searchParams: Promise<Record<string, string | string[] | undefined>> };

export default async function NewInquiryPage({ searchParams }: Props) {
  const sp = await searchParams;
  const raw = typeof sp.room === "string" ? sp.room.trim() : "";
  // a room label, never free text into the form: letters, digits, space, dash
  const room = /^[\p{L}\p{N} -]{1,20}$/u.test(raw) ? raw : undefined;
  return <NewInquiry room={room} />;
}
