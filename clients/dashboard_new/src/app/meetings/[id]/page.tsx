"use client";

import { useParams } from "next/navigation";
import { MeetingDetail } from "@/components/meeting-detail";

export default function MeetingDetailPage() {
  const params = useParams<{ id: string }>();
  const id = Array.isArray(params.id) ? params.id[0] : params.id;
  if (!id) return <div className="panel muted">No meeting id.</div>;
  return <MeetingDetail meetingId={id} />;
}
