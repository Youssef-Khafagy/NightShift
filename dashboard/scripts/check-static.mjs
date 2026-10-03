// Fails the build check if any public route can run code per request.
//
//     npm run build && npm run check:static
//
// The public replay is safe by construction only if it really is static:
// a page rendered per request is server code a visitor can trigger. Next.js
// records what it prerendered in .next/prerender-manifest.json, so this
// reads the facts after the build instead of trusting a "force-static" line.
//
// A route passes if it was prerendered with no revalidation, or if it is a
// dynamic segment whose every value was prerendered and any other value is a
// 404 (fallback: false). Only paths under PRIVATE may run per request; those
// are the signed-in live pages, each of which checks the session itself.

import { readFileSync } from "node:fs";

const PRIVATE = [];

const routes = Object.values(
  JSON.parse(readFileSync(".next/app-path-routes-manifest.json", "utf8")),
);
const prerender = JSON.parse(readFileSync(".next/prerender-manifest.json", "utf8"));

const problems = [];
for (const route of routes) {
  if (PRIVATE.some((prefix) => route === prefix || route.startsWith(`${prefix}/`))) {
    continue;
  }
  const page = prerender.routes[route];
  if (page) {
    if (page.initialRevalidateSeconds !== false) {
      problems.push(`${route} is regenerated on the server every ${page.initialRevalidateSeconds} s`);
    }
    continue;
  }
  const dynamic = prerender.dynamicRoutes[route];
  if (dynamic && dynamic.fallback === false) {
    continue;
  }
  problems.push(`${route} renders on request`);
}

if (problems.length) {
  console.error(`Public routes must be static:\n  ${problems.join("\n  ")}`);
  process.exit(1);
}
console.log(`${routes.length} routes, all static`);
