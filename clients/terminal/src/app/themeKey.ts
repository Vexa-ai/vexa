/** The persisted-theme storage key, alone in a React-free module.
 *
 *  Two runtimes need it: the `useTheme` client hook (theme.ts) and the pre-paint <script> the
 *  SERVER component in layout.tsx inlines into <head> to avoid a dark flash on reload. layout.tsx
 *  cannot import theme.ts — that module pulls in React hooks and a server component may not — so
 *  the key used to be repeated as a literal in both places, where a change to one silently stopped
 *  the saved theme from being restored. A constants module is reachable from both runtimes.
 */
export const THEME_KEY = "vexa.terminal.theme";
