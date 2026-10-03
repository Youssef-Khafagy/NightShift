# Demo

Two ways to show NightShift, depending on time and setting.

| | The replay | The live run |
|---|---|---|
| What | One recorded incident, played on the website: https://night-shift-tau-amber.vercel.app/demo | A real incident staged on the real store, approved by you on the Live page |
| How long | About a minute, or as long as you talk | 15 to 20 minutes, plus 45 minutes of preparation beforehand |
| Needs | A browser | Your laptop with `aws login`, and the Live page signed in |
| Risk | None: static files, no AWS, no model | Real traffic and a real fault, inside the free allowances; it can fail like anything real |
| Use it for | Interviews, the README, a screen recording | When someone wants to see it happen for real |

## The replay (the 60-second demo)

The Demo page tells one real incident in nine scenes: normal traffic, a broken deploy of orders, the alarm, the agent's 16 investigation steps, its diagnosis, the proposed rollback and its approval, the rollback and the confirmed recovery, and the postmortem. **Play** runs it in about a minute; **Next** and **Back** (or the arrow keys) let you talk over each scene. Everything on it comes from the record of the M6 live check on 2026-09-24.

To record the video: browser at 1280 by 800, open the Demo page, start the screen recording (Windows: Win+Alt+R), press Play, and read along:

| Scene | Say |
|---|---|
| One real incident | "NightShift is an AI on-call engineer for AWS. This is a real incident on the store I built, replayed as it happened." |
| Normal traffic | "The store is healthy, about one checkout a second." |
| A broken version ships | "A deploy with a one-word typo goes out. Every checkout now tells the customer it failed." |
| The page | "Ninety-six seconds later the alarm fires and pages the agent." |
| The investigation | "It works through sixteen steps with read-only tools: metrics, logs, recent deployments." |
| The diagnosis | "It names the cause: a bad deploy of orders, two minutes after the fault. Not perfect: it adds a claim its evidence doesn't show." |
| A human approves | "It can only propose. I approve this one exact action." |
| Rolled back, and checked | "A separate component rolls back and watches the alarm until it recovers." |
| The postmortem | "And it writes the postmortem. How often is it right? Thirty-six incidents, against a scripted runbook, are on the Results page." |

That is 136 words, about a minute at a normal speaking pace.

Don't say: that it beats the runbook (it doesn't, measurably); that it is safe because it is accurate (it is safe because it can only read, can only propose from an allowlist, and needs your approval); that the replay is live.

## The live run

The website never starts a real incident: that would mean giving a public site permission to break the store. Your laptop starts it, the Live page follows it stage by stage, and you approve there.

**At least 45 minutes before:**

```bash
aws login --profile nightshift-admin
python scripts/demo.py prepare
```

This switches the queue consumer and the agent trigger on. The chaos runner then needs 45 quiet minutes before it will inject, so that nothing the agent reads is left over from something else. `python scripts/demo.py status` shows when the gap ends.

**When you are ready:** open the Live page (https://night-shift-tau-amber.vercel.app/live), sign in, then:

```bash
python scripts/demo.py start
```

What happens, and roughly when:

1. Three minutes of normal traffic. The Live page's tracker shows "Traffic".
2. The broken version of orders ships. About a minute and a half later the orders-errors alarm fires.
3. The agent investigates in Lambda for about a minute. Its journal appears on the Live page.
4. It proposes `rollback_alias service=orders`. On the Live page: **Approve...**, then **Run exactly this action**.
5. The Actor rolls orders back and watches the alarm, up to 10 minutes; usually about three.
6. The runner recovers anything left, checks the store's health, and prints the result.

**Afterwards:**

```bash
python scripts/demo.py stop
```

This switches the trigger and the consumer off. Ten minutes later, `python scripts/pause.py` proves the store is idle.

**If something goes wrong:** the runner always recovers what it changed, even when interrupted (`python -m chaos.run --restore results/chaos/<run>/state.json` if it was killed). If the agent gets it wrong, say so: that is what the benchmark measures, and the Results page shows how often.

**Cost:** at most about 1,260 checkouts, 5,900 Lambda invocations, 315 DSQL DPU and ten model calls, under 1% of each free allowance.
