import { VexaAPIError } from "@/lib/api";
import { serviceDenialFromError } from "@/lib/service-denial";

export interface UserFriendlyError {
  title: string;
  description: string;
}

/**
 * Converts API errors into user-friendly messages
 */
export function getUserFriendlyError(error: Error): UserFriendlyError {
  const message = error.message.toLowerCase();

  // Service-authority denial — billing, spend cap, concurrency ceiling. These
  // arrive as 403/503 with a `service_not_allowed` code and must never be
  // flattened into "Access denied": the customer cannot act on that, and a
  // paywall then looks identical to an outage. The join surfaces render this
  // as a panel (see `resolveJoinError`); this branch is the fallback for
  // callers that only have a title/description to show.
  const denial = serviceDenialFromError(error);
  if (denial) {
    return { title: denial.title, description: denial.body };
  }

  // Concurrent bot limit reached — the 0.10 core states it as a plain string
  // with the numbers in it ("Concurrent bot limit reached (2/3)"), so say them.
  if (message.includes("concurrent") && message.includes("limit")) {
    return {
      title: "Bot limit reached",
      description: concurrencyLimitDescription(error.message),
    };
  }

  // Rate limiting
  if (message.includes("rate limit") || message.includes("too many requests")) {
    return {
      title: "Too many requests",
      description: "Please wait a moment before trying again.",
    };
  }

  // Authentication errors
  if (error instanceof VexaAPIError && error.status === 401) {
    return {
      title: "Authentication failed",
      description: "Your session may have expired. Please log in again.",
    };
  }

  // Forbidden — a GENUINE permission fault. Service denials never reach here;
  // they are handled above.
  if (error instanceof VexaAPIError && error.status === 403) {
    return {
      title: "Access denied",
      description: error.message || "You don't have permission to perform this action.",
    };
  }

  // Server errors
  if (error instanceof VexaAPIError && error.status >= 500) {
    return {
      title: "Server error",
      description: "The server encountered an issue. Please try again later.",
    };
  }

  // Network errors
  if (message.includes("network") || message.includes("fetch")) {
    return {
      title: "Connection error",
      description: "Unable to connect to the server. Please check your internet connection.",
    };
  }

  // Default error
  return {
    title: "Something went wrong",
    description: error.message || "An unexpected error occurred.",
  };
}

/**
 * Keeps the ceiling the server named. `(2/3)` means two running against a
 * limit of three; without it we can only describe the state.
 */
function concurrencyLimitDescription(rawMessage: string): string {
  const match = /\((\d+)\s*\/\s*(\d+)\)/.exec(rawMessage);
  if (match) {
    const [, active, limit] = match;
    return `Your plan runs ${limit} bot${limit === "1" ? "" : "s"} at once and ${active} ${active === "1" ? "is" : "are"} already in a meeting. Stop one to start another.`;
  }
  const bare = /limit\s*\(?\s*(\d+)\s*\)?\s*\.?$/i.exec(rawMessage.trim());
  if (bare) {
    const limit = bare[1];
    return `Your plan runs ${limit} bot${limit === "1" ? "" : "s"} at once and they are all in meetings. Stop one to start another.`;
  }
  return "You have reached your maximum number of concurrent bots. Stop an existing bot to start a new one.";
}
