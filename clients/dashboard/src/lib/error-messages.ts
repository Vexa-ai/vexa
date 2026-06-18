import { VexaAPIError } from "@/lib/api";

export interface UserFriendlyError {
  title: string;
  description: string;
}

/**
 * Converts API errors into user-friendly messages
 */
export function getUserFriendlyError(error: Error): UserFriendlyError {
  const message = error.message.toLowerCase();

  // Concurrent bot limit reached
  if (message.includes("concurrent") && message.includes("limit")) {
    return {
      title: "Bot limit reached",
      description: "You have reached your maximum number of concurrent bots. Stop an existing bot to start a new one.",
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

  // Forbidden
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
