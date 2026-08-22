import { T } from "./tokens";

/** The document rail may consume every pixel the shell can spare. Its ceiling is the
 *  current window, not an arbitrary panel width; the conversation keeps its readable floor. */
export function maxPagesWidth(viewportWidth: number): number {
  return Math.max(T.pagesMin, viewportWidth - T.railW - T.conversationMin);
}

export function clampPagesWidth(width: number, viewportWidth: number): number {
  return Math.min(maxPagesWidth(viewportWidth), Math.max(T.pagesMin, width));
}
