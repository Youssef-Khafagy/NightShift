// The checks every live route makes itself, before anything touches AWS.
// Not in middleware: a check that lives only in middleware has been
// bypassed before (CVE-2025-29927), and a route that checks for itself
// stays safe whatever runs in front of it.

import { getServerSession } from "next-auth";

import { authOptions } from "./auth";
import { isOwner } from "./owner";

export type Owner = { githubId: string; login: string };

export function json(body: unknown, status = 200): Response {
  return Response.json(body, {
    status,
    headers: { "Cache-Control": "no-store" },
  });
}

// The owner's identity, or the response that refuses the request.
export async function requireOwner(): Promise<Owner | Response> {
  const session = await getServerSession(authOptions);
  if (!session) return json({ error: "sign in first" }, 401);
  if (!isOwner(session.githubId)) return json({ error: "not allowed" }, 403);
  return { githubId: session.githubId as string, login: session.login ?? "owner" };
}

// A write must come from this site's own pages, as JSON. The session cookie
// is SameSite=Lax, which already stops another site's form from carrying
// it; checking Origin and the content type means a cross-site request
// fails even if that ever changed.
export function sameOriginJson(request: Request): boolean {
  const origin = request.headers.get("origin");
  const type = request.headers.get("content-type") ?? "";
  return origin === new URL(request.url).origin && type.startsWith("application/json");
}
