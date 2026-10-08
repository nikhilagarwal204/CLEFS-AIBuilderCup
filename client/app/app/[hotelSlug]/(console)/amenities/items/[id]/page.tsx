import ItemTrend from "../../_components/ItemTrend";

/* /app/[hotelSlug]/amenities/items/[id]: one item's trend, by floor and by
   shift (02 spec 5.3). The id is validated client side (digits only). */
export default async function AmenityItemPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ItemTrend itemId={id} />;
}
