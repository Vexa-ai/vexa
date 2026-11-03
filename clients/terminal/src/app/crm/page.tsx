import { CrmRecordPage } from "../../surfaces/crmRecord";

export const dynamic = "force-dynamic";

/** `/crm?record=<id>` — the one CRM screen (ADR-0047 d8): a record's fields, history, the pages that
 *  mention it and its pending proposals. A deployment without the CRM answers 404 at the edge, and
 *  the page says so. */
export default async function CrmPage({ searchParams }: { searchParams: Promise<{ record?: string }> }) {
  const query = await searchParams;
  return <CrmRecordPage recordId={query.record || ""} />;
}
