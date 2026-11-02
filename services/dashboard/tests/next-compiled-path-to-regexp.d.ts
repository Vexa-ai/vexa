// Next 16.4 ships its vendored path-to-regexp (next/dist/compiled/path-to-regexp) without type
// declarations, and tsconfig includes tests/, so `next build` failed its type check on the
// import in test_security_headers.test.ts (#1969). This declares the one function that test calls.
declare module "next/dist/compiled/path-to-regexp" {
  export function pathToRegexp(
    path: string,
    keys?: unknown[],
    options?: { strict?: boolean; sensitive?: boolean; delimiter?: string; start?: boolean; end?: boolean },
  ): RegExp;
}
