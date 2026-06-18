import { MetadataRoute } from "next";

export default function sitemap(): MetadataRoute.Sitemap {
  const baseUrl = process.env.NEXT_PUBLIC_BASE_URL || "https://dashboard.vexa.ai";

  // Main pages
  const mainPages = [
    "",
    "/login",
    "/join",
    "/meetings",
    "/settings",
  ];

  // Docs pages
  const docsPages = [
    "/docs",
    "/docs/auth",
    "/docs/rest/meetings",
    "/docs/rest/bots",
    "/docs/rest/transcripts",
    "/docs/ws",
    "/docs/ws/subscribe",
    "/docs/ws/events",
    "/docs/cookbook/start-transcription",
    "/docs/cookbook/track-meeting-status",
    "/docs/cookbook/get-transcripts",
    "/docs/cookbook/live-transcripts",
    "/docs/cookbook/share-transcript-url",
    "/docs/cookbook/rename-meeting",
    "/docs/cookbook/get-status-history",
    "/docs/cookbook/stop-bot",
    "/docs/admin/users",
    "/docs/admin/tokens",
  ];

  const pages = [
    ...mainPages.map((path) => ({
      url: `${baseUrl}${path}`,
      lastModified: new Date(),
      changeFrequency: "weekly" as const,
      priority: path === "" ? 1.0 : 0.8,
    })),
    ...docsPages.map((path) => ({
      url: `${baseUrl}${path}`,
      lastModified: new Date(),
      changeFrequency: "monthly" as const,
      priority: path === "/docs" ? 0.9 : 0.7,
    })),
  ];

  return pages;
}

