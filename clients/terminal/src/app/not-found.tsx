/** Any path with no page. Next's built-in 404 draws itself with style attributes and an inline
 *  `<style>`, both of which the page's policy refuses (contentSecurityPolicy.ts), so the terminal
 *  draws its own: classes only. */
import "./gate.css";

export default function NotFound() {
  return (
    <div className="bg-0 vx-gate-center">
      <div className="vx-notfound">
        <h1 className="vx-notfound-code">404</h1>
        <h2 className="vx-notfound-text">This page could not be found.</h2>
      </div>
    </div>
  );
}
