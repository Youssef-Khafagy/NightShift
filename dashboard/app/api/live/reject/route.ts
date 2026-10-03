import { awsClients } from "@/lib/live/aws";
import { readRejection, reject } from "@/lib/live/decide";
import { json, requireOwner, sameOriginJson } from "@/lib/live/guard";

export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  const owner = await requireOwner();
  if (owner instanceof Response) return owner;
  if (!sameOriginJson(request)) return json({ error: "refused" }, 403);
  const input = readRejection(await request.json().catch(() => null));
  if (!input) return json({ error: "bad request" }, 400);
  const now = Math.floor(Date.now() / 1000);
  const decision = await reject(awsClients(), input, `github:${owner.login}`, now);
  return json(decision.body, decision.status);
}
