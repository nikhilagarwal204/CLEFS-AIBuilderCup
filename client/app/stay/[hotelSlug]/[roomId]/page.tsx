import ClefsConcierge from "../../../ClefsConcierge";

interface HotelRoomConciergeProps {
  params: Promise<{
    hotelSlug: string;
    roomId: string;
  }>;
}

export default async function HotelRoomConcierge({ params }: HotelRoomConciergeProps) {
  const resolvedParams = await params;
  const rawId = resolvedParams.roomId || "";
  const normalized = rawId.toLowerCase().startsWith("room") ? rawId.slice(4) : rawId;

  // Non-numeric slug (e.g. "public", "sky-lounge") → location mode
  const isLocation = !/^\d+$/.test(normalized);

  if (isLocation) {
    return <ClefsConcierge location={normalized} hotelSlug={resolvedParams.hotelSlug} />;
  }

  return <ClefsConcierge roomNumber={normalized} hotelSlug={resolvedParams.hotelSlug} />;
}
