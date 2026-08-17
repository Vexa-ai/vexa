import { create } from "zustand";
import { adminAPI } from "@/lib/admin-api";
import type {
  VexaUser,
  VexaUserWithTokens,
  CreateUserRequest,
  UpdateUserRequest,
  CreateTokenResponse,
} from "@/types/vexa";

interface AdminState {
  // Data
  users: VexaUser[];
  selectedUser: VexaUserWithTokens | null;

  // Loading states
  isLoadingUsers: boolean;
  isLoadingUser: boolean;
  isCreatingUser: boolean;
  isCreatingToken: boolean;

  // Error
  error: string | null;

  // Last created token (shown only once)
  lastCreatedToken: CreateTokenResponse | null;

  // Actions
  fetchUsers: () => Promise<void>;
  fetchUser: (userId: string) => Promise<void>;
  createUser: (data: CreateUserRequest) => Promise<VexaUser | null>;
  updateUser: (userId: string, data: UpdateUserRequest) => Promise<void>;
  createToken: (userId: string) => Promise<CreateTokenResponse | null>;
  revokeToken: (tokenId: string) => Promise<void>;
  clearSelectedUser: () => void;
  clearLastCreatedToken: () => void;
  clearError: () => void;
}

export const useAdminStore = create<AdminState>((set, get) => ({
  users: [],
  selectedUser: null,
  isLoadingUsers: false,
  isLoadingUser: false,
  isCreatingUser: false,
  isCreatingToken: false,
  error: null,
  lastCreatedToken: null,

  fetchUsers: async () => {
    set({ isLoadingUsers: true, error: null });
    try {
      const users = await adminAPI.getUsers();
      set({ users, isLoadingUsers: false });
    } catch (error) {
      set({
        error: (error as Error).message,
        isLoadingUsers: false,
      });
    }
  },

  fetchUser: async (userId: string) => {
    set({ isLoadingUser: true, error: null });
    try {
      const user = await adminAPI.getUser(userId);
      set({ selectedUser: user, isLoadingUser: false });
    } catch (error) {
      set({
        error: (error as Error).message,
        isLoadingUser: false,
      });
    }
  },

  createUser: async (data: CreateUserRequest) => {
    set({ isCreatingUser: true, error: null });
    try {
      const user = await adminAPI.createUser(data);
      // Add to users list
      set((state) => ({
        users: [user, ...state.users],
        isCreatingUser: false,
      }));
      return user;
    } catch (error) {
      set({
        error: (error as Error).message,
        isCreatingUser: false,
      });
      return null;
    }
  },

  updateUser: async (userId: string, data: UpdateUserRequest) => {
    set({ error: null });
    try {
      const updatedUser = await adminAPI.updateUser(userId, data);
      // Update in users list
      set((state) => ({
        users: state.users.map((u) => (u.id === userId ? updatedUser : u)),
        selectedUser:
          state.selectedUser?.id === userId
            ? { ...state.selectedUser, ...updatedUser }
            : state.selectedUser,
      }));
    } catch (error) {
      set({ error: (error as Error).message });
    }
  },

  createToken: async (userId: string) => {
    set({ isCreatingToken: true, error: null });
    try {
      const token = await adminAPI.createToken(userId);
      set({ lastCreatedToken: token, isCreatingToken: false });
      // Refresh user to get updated token list
      await get().fetchUser(userId);
      return token;
    } catch (error) {
      set({
        error: (error as Error).message,
        isCreatingToken: false,
      });
      return null;
    }
  },

  revokeToken: async (tokenId: string) => {
    set({ error: null });
    try {
      await adminAPI.revokeToken(tokenId);
      // Update selected user's tokens
      set((state) => ({
        selectedUser: state.selectedUser
          ? {
              ...state.selectedUser,
              api_tokens: state.selectedUser.api_tokens.filter(
                (t) => t.id !== tokenId
              ),
            }
          : null,
      }));
    } catch (error) {
      set({ error: (error as Error).message });
    }
  },

  clearSelectedUser: () => set({ selectedUser: null }),
  clearLastCreatedToken: () => set({ lastCreatedToken: null }),
  clearError: () => set({ error: null }),
}));
