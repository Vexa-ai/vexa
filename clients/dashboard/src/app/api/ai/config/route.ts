import { NextResponse } from "next/server";

export const runtime = "nodejs";

// Parse AI_MODEL env var format: "provider/model" (e.g., "openai/gpt-4o")
function parseAIModel(): { provider: string; model: string } | null {
  const aiModel = process.env.AI_MODEL;
  if (!aiModel) return null;

  const [provider, ...modelParts] = aiModel.split("/");
  const model = modelParts.join("/");

  if (!provider || !model) return null;

  return { provider: provider.toLowerCase(), model };
}

export async function GET() {
  const config = parseAIModel();

  if (!config) {
    return NextResponse.json({
      enabled: false,
      provider: null,
      model: null,
    });
  }

  // Don't expose the API key, just indicate it's configured
  const hasApiKey = !!process.env.AI_API_KEY;
  const hasBaseUrl = !!process.env.AI_BASE_URL;

  return NextResponse.json({
    enabled: true,
    provider: config.provider,
    model: config.model,
    hasApiKey,
    hasBaseUrl,
  });
}
