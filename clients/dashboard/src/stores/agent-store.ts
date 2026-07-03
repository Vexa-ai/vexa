import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

export interface AgentMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  tools?: { tool: string; summary: string }[];
  timestamp: number;
}

export interface AgentSession {
  id: string;
  name: string;
  created_at?: number;
  updated_at?: number;
  messages: AgentMessage[];
}

interface AgentState {
  sessions: AgentSession[];
  activeSessionId: string | null;
  isStreaming: boolean;
  userId: string;
  sessionsLoaded: boolean;

  // Session CRUD
  loadSessions: (userId: string) => Promise<void>;
  createSession: (name: string) => Promise<string | null>;
  deleteSession: (sessionId: string) => Promise<void>;
  renameSession: (sessionId: string, name: string) => Promise<void>;
  setActiveSession: (sessionId: string | null) => void;

  // Messages (operate on active session)
  messages: AgentMessage[];
  addMessage: (msg: AgentMessage) => void;
  updateLastAssistant: (content: string, tools?: { tool: string; summary: string }[]) => void;
  setStreaming: (v: boolean) => void;
  setUserId: (id: string) => void;
  clearMessages: () => void;
}

const AGENT_API = "/api/agent";

export const useAgentStore = create<AgentState>()(
  persist(
    (set, get) => ({
      sessions: [],
      activeSessionId: null,
      isStreaming: false,
      userId: "default",
      sessionsLoaded: false,
      messages: [],

      loadSessions: async (userId: string) => {
        try {
          const resp = await fetch(`${AGENT_API}/sessions?user_id=${userId}`);
          if (resp.ok) {
            const data = await resp.json();
            const remoteSessions: AgentSession[] = (data.sessions || []).map((s: any) => ({
              id: s.id,
              name: s.name || `Session ${s.id.slice(0, 8)}`,
              created_at: s.created_at,
              updated_at: s.updated_at,
              messages: [],  // Messages stay client-side for now
            }));

            set((state) => {
              // Merge: keep local messages for sessions we already have
              const merged = remoteSessions.map((remote) => {
                const local = state.sessions.find((s) => s.id === remote.id);
                return local ? { ...remote, messages: local.messages } : remote;
              });
              return { sessions: merged, sessionsLoaded: true };
            });
          }
        } catch {
          set({ sessionsLoaded: true });
        }
      },

      createSession: async (name: string) => {
        const { userId } = get();
        try {
          const resp = await fetch(`${AGENT_API}/sessions?user_id=${userId}&name=${encodeURIComponent(name)}`, {
            method: "POST",
          });
          if (resp.ok) {
            const data = await resp.json();
            const session: AgentSession = {
              id: data.session_id,
              name,
              created_at: Date.now() / 1000,
              updated_at: Date.now() / 1000,
              messages: [],
            };
            set((state) => ({
              sessions: [session, ...state.sessions],
              activeSessionId: session.id,
              messages: [],
            }));
            return session.id;
          }
        } catch {}
        return null;
      },

      deleteSession: async (sessionId: string) => {
        const { userId } = get();
        try {
          await fetch(`${AGENT_API}/sessions/${sessionId}?user_id=${userId}`, {
            method: "DELETE",
          });
        } catch {}
        set((state) => {
          const sessions = state.sessions.filter((s) => s.id !== sessionId);
          const newActive = state.activeSessionId === sessionId
            ? (sessions[0]?.id || null)
            : state.activeSessionId;
          const msgs = sessions.find((s) => s.id === newActive)?.messages || [];
          return { sessions, activeSessionId: newActive, messages: msgs };
        });
      },

      renameSession: async (sessionId: string, name: string) => {
        const { userId } = get();
        try {
          await fetch(`${AGENT_API}/sessions/${sessionId}?user_id=${userId}&name=${encodeURIComponent(name)}`, {
            method: "PUT",
          });
        } catch {}
        set((state) => ({
          sessions: state.sessions.map((s) =>
            s.id === sessionId ? { ...s, name } : s
          ),
        }));
      },

      setActiveSession: (sessionId: string | null) => {
        set((state) => {
          // Save current messages to current session before switching
          const sessions = state.sessions.map((s) =>
            s.id === state.activeSessionId
              ? { ...s, messages: state.messages }
              : s
          );
          const newMessages = sessions.find((s) => s.id === sessionId)?.messages || [];
          return { sessions, activeSessionId: sessionId, messages: newMessages };
        });
      },

      addMessage: (msg) =>
        set((s) => ({ messages: [...s.messages, msg] })),

      updateLastAssistant: (content, tools) =>
        set((s) => {
          const msgs = [...s.messages];
          const last = msgs[msgs.length - 1];
          if (last && last.role === "assistant") {
            msgs[msgs.length - 1] = { ...last, content, tools: tools || last.tools };
          }
          return { messages: msgs };
        }),

      setStreaming: (v) => set({ isStreaming: v }),
      setUserId: (id) => set({ userId: id }),
      clearMessages: () => set({ messages: [] }),
    }),
    {
      name: "vexa-agent-chat",
      storage: createJSONStorage(() => sessionStorage),
      partialize: (state) => ({
        sessions: state.sessions,
        activeSessionId: state.activeSessionId,
        messages: state.messages,
        userId: state.userId,
      }),
    }
  )
);
