/** `/join/<token>` — the spelling a join link is handed out in (#1783): a calendar event, an email.
 *  It is the same link as `/join?i=<token>`, and the page that redeems it is that one. */
import { redirect } from "next/navigation";
import { joinPathFor } from "../joinState";

export const dynamic = "force-dynamic";

export default async function JoinByPath({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  redirect(joinPathFor(token));
}
