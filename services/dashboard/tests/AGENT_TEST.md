# Agent Test: Dashboard

## Prerequisites
- Services running: dashboard, api-gateway, admin-api, postgres (Docker)
- Environment: Dashboard accessible at http://localhost:3000 (or configured port)
- Setup: `docker compose up -d`

## Tests

### Test 1: Page Load and Navigation
**Goal:** Verify all dashboard pages load without errors.
**Setup:** Open the dashboard in a browser or use curl to fetch each page route.
**Verify:** Each page returns 200. No JavaScript console errors. Key UI elements render.
**Evidence:** Capture HTTP status codes for each route. Note any console errors or missing assets.
**Pass criteria:** All pages return 200. No broken asset references (404 for JS/CSS/images).

### Test 2: API Integration
**Goal:** Verify the dashboard correctly communicates with the API gateway.
**Setup:** Log in (or use a valid session) and navigate to pages that fetch data from the API.
**Verify:** Data loads and displays correctly. No CORS errors in browser console. Loading states and error states are handled.
**Evidence:** Capture network tab showing API calls and their responses. Note any failed requests.
**Pass criteria:** All API calls return expected data. No CORS errors. Error states display meaningful messages to the user.

### Test 3: Environment Variable Safety
**Goal:** Verify no sensitive values are exposed to the client.
**Setup:** View the page source and JavaScript bundles. Search for NEXT_PUBLIC_ prefixed variables.
**Verify:** Only non-sensitive configuration values use NEXT_PUBLIC_ prefix. No API keys, tokens, or secrets in client bundles.
**Evidence:** List all NEXT_PUBLIC_ variables found in the built JavaScript. Capture any suspicious values.
**Pass criteria:** Zero secrets, API keys, or tokens in client-side code. All NEXT_PUBLIC_ values are safe for public exposure.

### Test 4: Responsive Layout
**Goal:** Verify the dashboard renders correctly at different viewport sizes.
**Setup:** View the dashboard at: desktop (1920x1080), tablet (768x1024), mobile (375x667).
**Verify:** Layout adapts appropriately. No overlapping elements. All interactive elements are accessible.
**Evidence:** Capture screenshots at each viewport size.
**Pass criteria:** Content is readable and usable at all sizes. No horizontal scrolling on mobile. Navigation is accessible.
