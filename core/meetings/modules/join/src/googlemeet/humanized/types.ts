// Clean-room humanized-input types (Apache-2.0). Independent reimplementation of
// the publicly described "humanized mouse motion" approach for defeating Google
// Meet bot-detection. No third-party source or recorded data is used.

export interface MocapMovement {
  dx: number; // relative pixel delta, x
  dy: number; // relative pixel delta, y
  dt: number; // seconds to wait BEFORE issuing this move
}

export interface MocapSequence {
  movements: MocapMovement[];
  total_dx: number;
  total_dy: number;
  click_down_dt: number; // seconds between arrival and button-down
  click_up_dt: number; // seconds button held before release
}

export interface MocapLibrary {
  meta: Record<string, unknown>;
  sequences: MocapSequence[];
}

export type UiInteractionMode = "humanized" | "synthetic";
