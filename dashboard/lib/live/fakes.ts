// Fake AWS clients for tests: each records the commands it was sent and
// answers from a function the test provides. Never used by the app.

import { vi } from "vitest";

import type { Clients } from "./aws";

export type Sent = { name: string; input: Record<string, unknown> };

export function fakeClients(answer: (sent: Sent) => unknown = () => ({})) {
  const sent: Sent[] = [];
  const client = {
    send: vi.fn(async (command: { constructor: { name: string }; input: Record<string, unknown> }) => {
      const s = { name: command.constructor.name, input: command.input };
      sent.push(s);
      const reply = answer(s);
      if (reply instanceof Error) throw reply;
      return reply;
    }),
  };
  const clients = { cw: client, ddb: client, lam: client } as unknown as Clients;
  return { clients, sent };
}
