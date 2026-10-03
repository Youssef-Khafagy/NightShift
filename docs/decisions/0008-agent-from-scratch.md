# ADR 0008: The agent is built from scratch, with no framework and no SDKs

Status: Accepted by the owner, 2026-09-18 (design) and 2026-09-23 (M5 steps 2 and 4). Recorded as an ADR 2026-10-03 (M8).

## Context

The agent has to do a few specific things that frameworks either hide or make harder: stop on hard limits (steps, tokens, wall-clock seconds), save a checkpoint after every step so a crashed or timed-out Lambda resumes where it stopped, keep a journal of every tool call that a grader and a postmortem can cite by step number, and treat every tool result as untrusted data. It runs in a 256 MB Lambda where every imported package costs init time (imports at 128 MB measured 11.9 s inside a handler and 0.7 s at module scope).

It also has to work on free tiers from different vendors. The binding limit there is per request: Groq's free tier allows 8,000 tokens a minute, which is also the largest single request that can ever be sent.

And the owner must be able to explain every line.

## Decision

- The loop is about 75 lines (`Investigator.run` in `agent/loop.py`): rebuild the conversation from the saved state, send it with the tool definitions, run up to three tool calls the model asks for, save a checkpoint; stop on `finish_investigation` or a limit, and a stop on a limit is itself an answer (`insufficient_evidence`, with the reason).
- The conversation is never stored. It is rebuilt each turn from the journal, which is what makes resuming after a crash cheap.
- Three providers (Gemini, Groq, Mistral) behind one interface, over standard-library HTTPS, with no vendor SDKs. A 429 waits for `retry-after`, never past the wall clock. Swapping models is a config change.
- Only the three most recent tool results are kept in full, older ones as one-line summaries, and the request size is estimated before sending.

## Options considered

| Option | Why not |
|---|---|
| LangChain, LangGraph, CrewAI | Hide the loop the benchmark is measuring; checkpointing and limits would be their abstractions, not ours; large dependency trees in a Lambda layer. |
| Vendor SDKs | Three SDKs for three request formats that are two formats in practice (OpenAI-style and Gemini); more init time and more to pin. |
| **A loop of our own** | Chosen. |

## Consequences

- A Mistral investigation killed with `SIGKILL` after two steps resumed from DynamoDB at step three with nothing repeated.
- Each provider's recorded responses are test fixtures, so the translations are tested without a network.
- Provider differences become our problem: Gemini signs its function calls and rejects an unsigned one replayed into the conversation (HTTP 400), which the alarm-only baseline hit and now avoids.
- The pieces a framework would have provided are code we own, test and can explain: the loop, its context window and state, and the three providers come to 1,170 lines with comments (counted 2026-10-03).
