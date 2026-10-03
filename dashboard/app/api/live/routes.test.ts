// The live routes refuse before anything reaches AWS: no session is 401,
// another account is 403, a write from another site is 403. In each refused
// case the AWS clients are never even created.

import { readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";

import { getServerSession } from "next-auth";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { awsClients } from "@/lib/live/aws";
import { fakeClients } from "@/lib/live/fakes";
import { OWNER_GITHUB_ID } from "@/lib/live/owner";

import { POST as approvePOST } from "./approve/route";
import { POST as rejectPOST } from "./reject/route";
import { GET as statusGET } from "./status/route";

vi.mock("next-auth", () => ({ getServerSession: vi.fn() }));
vi.mock("@/lib/live/auth", () => ({ authOptions: {} }));
vi.mock("@/lib/live/aws", () => ({ awsClients: vi.fn() }));

const session = vi.mocked(getServerSession);
const clients = vi.mocked(awsClients);
const SITE = "https://night-shift.example";

function post(route: string, body: unknown, origin = SITE): Request {
  return new Request(`${SITE}/api/live/${route}`, {
    method: "POST",
    headers: { origin, "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

beforeEach(() => {
  session.mockReset();
  clients.mockReset();
  vi.stubEnv("NEXTAUTH_SECRET", "test-secret");
  vi.stubEnv("GITHUB_ID", "test-id");
  vi.stubEnv("GITHUB_SECRET", "test-secret");
});

describe("on a deployment without the sign-in secrets", () => {
  it("answers 503 from every live route, before reading any session", async () => {
    vi.stubEnv("NEXTAUTH_SECRET", "");
    const responses = await Promise.all([
      statusGET(new Request(`${SITE}/api/live/status`)),
      approvePOST(post("approve", {})),
      rejectPOST(post("reject", {})),
    ]);
    expect(responses.map((r) => r.status)).toEqual([503, 503, 503]);
    expect(session).not.toHaveBeenCalled();
    expect(clients).not.toHaveBeenCalled();
  });
});

describe("without the owner's session", () => {
  it("refuses every live route with 401, and never creates an AWS client", async () => {
    session.mockResolvedValue(null);
    const responses = await Promise.all([
      statusGET(new Request(`${SITE}/api/live/status`)),
      approvePOST(post("approve", {})),
      rejectPOST(post("reject", {})),
    ]);
    expect(responses.map((r) => r.status)).toEqual([401, 401, 401]);
    expect(clients).not.toHaveBeenCalled();
  });

  it("refuses another GitHub account with 403", async () => {
    session.mockResolvedValue({ githubId: "1", login: "someone", expires: "" });
    const r = await statusGET(new Request(`${SITE}/api/live/status`));
    expect(r.status).toBe(403);
    expect(clients).not.toHaveBeenCalled();
  });
});

describe("with the owner's session", () => {
  beforeEach(() => {
    session.mockResolvedValue({ githubId: OWNER_GITHUB_ID, login: "owner", expires: "" });
  });

  it("refuses a write from another site, before reading anything", async () => {
    const r = await rejectPOST(post("reject", { investigation_id: "abc", item: "approval#1" }, "https://evil.example"));
    expect(r.status).toBe(403);
    expect(clients).not.toHaveBeenCalled();
  });

  it("refuses a malformed request with 400", async () => {
    const r = await approvePOST(post("approve", { investigation_id: "abc", item: "checkpoint" }));
    expect(r.status).toBe(400);
    expect(clients).not.toHaveBeenCalled();
  });

  it("rejects an approval as github:<login>", async () => {
    const fake = fakeClients();
    clients.mockReturnValue(fake.clients);
    const r = await rejectPOST(post("reject", { investigation_id: "abc", item: "approval#1" }));
    expect(r.status).toBe(200);
    const values = fake.sent[0].input.ExpressionAttributeValues as Record<string, { S?: string }>;
    expect(values[":by"].S).toBe("github:owner");
  });

  it("refuses a bad investigation id on the status route", async () => {
    const r = await statusGET(new Request(`${SITE}/api/live/status?investigation=../x`));
    expect(r.status).toBe(400);
  });
});

describe("every live route checks the session itself", () => {
  it("calls requireOwner in each route file, not only somewhere upstream", () => {
    const dir = path.resolve(__dirname);
    const routes = readdirSync(dir)
      .filter((d) => statSync(path.join(dir, d)).isDirectory())
      .map((d) => path.join(dir, d, "route.ts"));
    expect(routes.length).toBeGreaterThanOrEqual(3);
    for (const file of routes) {
      expect(readFileSync(file, "utf8")).toContain("await requireOwner()");
    }
  });
});
