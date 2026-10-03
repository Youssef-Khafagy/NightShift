import { awsClients } from "@/lib/live/aws";
import { approve, readApproval } from "@/lib/live/decide";
import { json, requireOwner, sameOriginJson } from "@/lib/live/guard";

export const dynamic = "force-dynamic";

// A POST from this site's live page, by the owner, naming one approval and
// the hash of the action they were shown. Never a GET: a link can be
// opened by a mail scanner or a prefetch.
export async function POST(request: Request): Promise<Response> {
  const owner = await requireOwner();
  if (owner instanceof Response) return owner;
  if (!sameOriginJson(request)) return json({ error: "refused" }, 403);
  const input = readApproval(await request.json().catch(() => null));
  if (!input) return json({ error: "bad request" }, 400);
  const now = Math.floor(Date.now() / 1000);
  const decision = await approve(awsClients(), input, `github:${owner.login}`, now);
  return json(decision.body, decision.status);
}
