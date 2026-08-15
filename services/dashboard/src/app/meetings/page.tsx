import MeetingsClient from "./meetings-client";
import { loadInitialMeetingsPage } from "@/lib/meetings-page.server";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export default async function MeetingsPage() {
  const initialPage = await loadInitialMeetingsPage();
  return <MeetingsClient initialPage={initialPage} />;
}
