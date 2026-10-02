import { notFound } from "next/navigation";
import { CrmView } from "./view";
export const dynamic = "force-dynamic";
export default async function CrmPage({ searchParams }: { searchParams: Promise<{ record?: string }> }) {
  if (!process.env.CRM_API_URL) notFound();
  const query = await searchParams;
  return <CrmView initialRecord={query.record || ""} />;
}
