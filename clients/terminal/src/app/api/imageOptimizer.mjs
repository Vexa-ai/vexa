// The image optimizer is off (next.config.ts `images.unoptimized`, and the images ship no sharp), so
// `/_next/image` has nothing to serve. Next still sets up its optimizer cache for that route on the
// first request, which tries to create `.next/cache` in a runtime image whose app tree the process
// cannot write, and logs an unhandled EACCES. server.mjs answers the route itself instead.

/** Whether ``url`` (a request target) names the image optimizer route. */
export function isImageOptimizerPath(url) {
  const path = String(url || "").split("?", 1)[0];
  return path === "/_next/image" || path.startsWith("/_next/image/");
}

/** A 404 for the optimizer route, written without handing the request to Next. */
export function refuseImageOptimizer(res) {
  res.statusCode = 404;
  res.setHeader("content-type", "text/plain; charset=utf-8");
  res.setHeader("cache-control", "no-store");
  res.end("The image optimizer is off in this build.\n");
}
