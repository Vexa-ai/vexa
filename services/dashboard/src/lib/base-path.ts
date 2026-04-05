// Base path support for sub-path deployments (e.g. /vexa on a shared domain)
const rawBasePath = process.env.NEXT_PUBLIC_BASE_PATH || "";
const normalizedBasePath = rawBasePath.replace(/\/$/, "");

export const basePath = normalizedBasePath;

export function withBasePath(path: string): string {
  if (!normalizedBasePath) {
    return path;
  }
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  if (normalizedPath === "/") {
    return normalizedBasePath || "/";
  }
  return `${normalizedBasePath}${normalizedPath}`;
}

export function stripBasePath(path: string): string {
  if (!normalizedBasePath || !path.startsWith(normalizedBasePath)) {
    return path;
  }
  const stripped = path.slice(normalizedBasePath.length);
  return stripped === "" ? "/" : stripped;
}
