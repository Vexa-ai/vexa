/** ALLOY: Vexa currently supplies an empty placeholder participant list. Hide
 * its false zero only for opted-in builds; keep any future positive roster. */
export function shouldShowRoomCount(
  participantCount: number,
  flag = process.env.NEXT_PUBLIC_ALLOY_HIDE_EMPTY_ROOM_COUNT,
): boolean {
  return flag !== "1" || participantCount > 0;
}
