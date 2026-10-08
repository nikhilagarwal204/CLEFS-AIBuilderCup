/* Which menu items a guest may order from where they are. Pure, with type
   imports only, so node --test can load it. */

import type { GuestMenuItem, GuestOutlet } from "./FoodMenu";

/**
 * In a room, drop items from outlets that do not deliver to rooms.
 * WHY: the guest menu returned the lobby cafe's Cappuccino beside In-Room
 * Dining with an ADD button, an order no one would carry upstairs (QA
 * GS-15). An outlet the payload does not describe is kept (the server's
 * own filter stays the authority), and a location page (no room) shows
 * everything, since a guest in the lobby can order from the cafe.
 */
export function orderableHere(
  items: readonly GuestMenuItem[],
  outlets: readonly GuestOutlet[],
  room: string | number | null | undefined,
): GuestMenuItem[] {
  if (room == null || room === "") return [...items];
  const noRoomService = new Set(outlets.filter((o) => o.delivers_to_room === false).map((o) => o.key));
  if (noRoomService.size === 0) return [...items];
  return items.filter((i) => !noRoomService.has(i.outlet));
}
