import { create } from "zustand";

interface JoinModalState {
  isOpen: boolean;
  openModal: () => void;
  closeModal: () => void;
}

export const useJoinModalStore = create<JoinModalState>((set) => ({
  isOpen: false,
  openModal: () => set({ isOpen: true }),
  closeModal: () => set({ isOpen: false }),
}));
