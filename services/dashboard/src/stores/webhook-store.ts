import { create } from "zustand";

// ==========================================
// Webhook Types
// ==========================================

export type WebhookDeliveryStatus = "delivered" | "retrying" | "failed";

export interface WebhookDelivery {
  id: string;
  event: string;
  meeting_id: string;
  meeting_name: string;
  status: WebhookDeliveryStatus;
  attempts: number;
  max_attempts: number;
  response_status: number | null;
  response_time_ms: number | null;
  endpoint_url: string;
  created_at: string;
  last_attempt_at: string;
}

export interface WebhookDeliveryAttempt {
  attempt: number;
  timestamp: string;
  endpoint_url: string;
  response_status: number | null;
  response_time_ms: number | null;
  success: boolean;
}

export interface WebhookStats {
  total: number;
  delivered: number;
  retrying: number;
  failed: number;
}

export interface WebhookConfig {
  endpoint_url: string;
  signing_secret_masked: string;
  events: Record<string, boolean>;
}

interface WebhookState {
  // Deliveries list
  deliveries: WebhookDelivery[];
  stats: WebhookStats;
  isLoading: boolean;
  error: string | null;

  // Meeting-specific deliveries
  meetingDeliveries: WebhookDeliveryAttempt[];
  isLoadingMeetingDeliveries: boolean;

  // Webhook config
  config: WebhookConfig | null;
  isLoadingConfig: boolean;
  isSavingConfig: boolean;

  // Filters
  statusFilter: WebhookDeliveryStatus | "all";
  timeRange: "24h" | "7d" | "30d";

  // User context
  userId: number | null;
  setUserId: (id: number) => void;

  // Actions
  fetchDeliveries: () => Promise<void>;
  fetchMeetingDeliveries: (meetingId: string) => Promise<void>;
  fetchConfig: () => Promise<void>;
  saveConfig: (config: Partial<WebhookConfig>) => Promise<void>;
  testWebhook: (url: string) => Promise<{ success: boolean; status?: number; time_ms?: number; error?: string }>;
  rotateSecret: () => Promise<string>;
  setStatusFilter: (filter: WebhookDeliveryStatus | "all") => void;
  setTimeRange: (range: "24h" | "7d" | "30d") => void;
}

function maskSecret(secret: string): string {
  if (!secret) return "";
  if (secret.length <= 8) return "••••••••";
  return secret.slice(0, 6) + "••••" + secret.slice(-4);
}

export const useWebhookStore = create<WebhookState>((set, get) => ({
  deliveries: [],
  stats: { total: 0, delivered: 0, retrying: 0, failed: 0 },
  isLoading: false,
  error: null,
  meetingDeliveries: [],
  isLoadingMeetingDeliveries: false,
  config: null,
  isLoadingConfig: false,
  isSavingConfig: false,
  userId: null,
  statusFilter: "all",
  timeRange: "7d",

  setUserId: (id: number) => set({ userId: id }),

  fetchDeliveries: async () => {
    set({ isLoading: true, error: null });
    try {
      const { statusFilter, timeRange, userId } = get();
      const params = new URLSearchParams();
      if (statusFilter !== "all") params.set("status", statusFilter);
      params.set("time_range", timeRange);
      if (userId) params.set("userId", String(userId));

      const response = await fetch(`/api/webhooks/deliveries?${params}`);
      if (!response.ok) {
        // If endpoint doesn't exist yet, use empty state
        if (response.status === 404) {
          set({ deliveries: [], stats: { total: 0, delivered: 0, retrying: 0, failed: 0 }, isLoading: false });
          return;
        }
        throw new Error("Failed to fetch webhook deliveries");
      }
      const data = await response.json();
      set({
        deliveries: data.deliveries || [],
        stats: data.stats || { total: 0, delivered: 0, retrying: 0, failed: 0 },
        isLoading: false,
      });
    } catch (error) {
      set({ error: (error as Error).message, isLoading: false });
    }
  },

  fetchMeetingDeliveries: async (meetingId: string) => {
    set({ isLoadingMeetingDeliveries: true });
    try {
      const response = await fetch(`/api/webhooks/deliveries/${meetingId}`);
      if (!response.ok) {
        if (response.status === 404) {
          set({ meetingDeliveries: [], isLoadingMeetingDeliveries: false });
          return;
        }
        throw new Error("Failed to fetch meeting webhook deliveries");
      }
      const data = await response.json();
      set({ meetingDeliveries: data.attempts || [], isLoadingMeetingDeliveries: false });
    } catch {
      set({ meetingDeliveries: [], isLoadingMeetingDeliveries: false });
    }
  },

  fetchConfig: async () => {
    const { userId } = get();
    set({ isLoadingConfig: true });
    try {
      const params = userId ? `?userId=${userId}` : "";
      const response = await fetch(`/api/webhooks/config${params}`);
      if (!response.ok) {
        if (response.status === 404) {
          set({ config: null, isLoadingConfig: false });
          return;
        }
        throw new Error("Failed to fetch webhook config");
      }
      const data = await response.json();
      set({ config: data, isLoadingConfig: false });
    } catch {
      set({ config: null, isLoadingConfig: false });
    }
  },

  saveConfig: async (configUpdate) => {
    const { userId } = get();
    set({ isSavingConfig: true });
    try {
      const response = await fetch("/api/webhooks/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...configUpdate, userId }),
      });
      if (!response.ok) throw new Error("Failed to save webhook config");
      const data = await response.json();
      set({ config: data, isSavingConfig: false });
    } catch (error) {
      set({ isSavingConfig: false });
      throw error;
    }
  },

  testWebhook: async (url: string) => {
    const { userId } = get();
    const response = await fetch("/api/webhooks/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, userId }),
    });
    return response.json();
  },

  rotateSecret: async () => {
    const { userId } = get();
    const response = await fetch("/api/webhooks/rotate-secret", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ userId }),
    });
    if (!response.ok) throw new Error("Failed to rotate secret");
    const data = await response.json();
    // Update config with new secret
    const { config } = get();
    if (config) {
      set({ config: { ...config, signing_secret_masked: maskSecret(data.signing_secret) } });
    }
    return data.signing_secret;
  },

  setStatusFilter: (filter) => {
    set({ statusFilter: filter });
    get().fetchDeliveries();
  },

  setTimeRange: (range) => {
    set({ timeRange: range });
    get().fetchDeliveries();
  },
}));
