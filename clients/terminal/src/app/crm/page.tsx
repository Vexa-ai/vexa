import { notFound } from "next/navigation";
import { CrmView } from "./view";
export const dynamic = "force-dynamic";
export default async function CrmPage({ searchParams }: { searchParams: Promise<{ record?: string; object?: string; filters?: string; name?: string }> }) {
  if (!process.env.CRM_API_URL) notFound();
  const query = await searchParams;
  return <CrmView initialName={query.name || ""} initialRecord={query.record || ""} initialObject={query.object || ""} initialFilters={query.filters || "{}"} />;
}
