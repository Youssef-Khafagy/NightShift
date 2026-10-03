import { awsClients } from "@/lib/live/aws";
import { json, requireOwner } from "@/lib/live/guard";
import { readStatus } from "@/lib/live/status";
import { INVESTIGATION } from "@/lib/live/validate";

// Owner only, and never cached: it reads live AWS state.
export const dynamic = "force-dynamic";

export async function GET(request: Request): Promise<Response> {
  const owner = await requireOwner();
  if (owner instanceof Response) return owner;
  const requested = new URL(request.url).searchParams.get("investigation") ?? undefined;
  if (requested !== undefined && !INVESTIGATION.test(requested)) {
    return json({ error: "bad investigation id" }, 400);
  }
  return json(await readStatus(awsClients(), requested));
}
