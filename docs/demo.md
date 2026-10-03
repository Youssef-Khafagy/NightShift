# 60-second demo

A screen recording of the public replay, with narration. Everything shown is a recorded run; the narration says so, and every sentence below rests on a fact listed under it.

## Setup

- Browser at 1280 by 800, light theme, zoom 100%, no extensions visible. Start on the dashboard's results page, scrolled to the top.
- Record the screen and voice together (Windows: Win+Alt+R with Xbox Game Bar, or OBS). Read the narration once aloud before recording; it is 150 words, about 60 seconds at a normal speaking pace of 150 a minute.
- One take per shot is fine; cut between shots.

## Shots and narration

| Time | Screen | Narration |
|---|---|---|
| 0:00 to 0:08 | Results page, top: the title and the accuracy chart. | "NightShift is an AI on-call engineer for AWS. I built a small store, broke it on purpose 36 times, and had five configurations investigate every incident." |
| 0:08 to 0:18 | Hover the Agent, Gemini row, then the Scripted runbook row. | "The Gemini agent was right half the time, a scripted runbook 58%. At this size, those can't be told apart." |
| 0:18 to 0:24 | Scroll to "By scenario". Point at row 6, IAM regression: the agent column against the runbook's 0/3. | "They win in different places. Here's one the runbook can't do." |
| 0:24 to 0:32 | Click "6. IAM regression", then Incident 10. Show the description and the "Paged with" card. | "Someone removed orders' permission to publish to its queue. Checkouts fail, and the page goes out two minutes later." |
| 0:32 to 0:46 | Scroll to "Investigations, step by step", tab Agent, Gemini, press "Replay at 10x speed". Let it run; open step 15's result. | "The agent finds no deploy, checks recent changes, metrics and traces, then finds AccessDenied on the queue in orders' own logs." |
| 0:46 to 0:54 | Scroll up to "The five answers". | "Orders, IAM regression, sixteen seconds after the page. It proposes nothing automatic, because no safe action fixes a permission. The runbook had no rule for this." |
| 0:54 to 1:00 | The footer, then the Method page's "What these numbers do not show". | "Every number here comes from recorded runs, and this site can't call AWS or a model. The code, the costs and the mistakes are in the repo." |

## What each line rests on

- **36 incidents, five configurations:** pass `m7`, commit `5641bb7`, 2026-09-30 to 2026-10-03 (`results/bench/m7/`).
- **Half the time, 58%, can't be told apart:** agent-gemini 18 of 36, runbook 21 of 36; on the incidents only one got right, 5 against 8, McNemar exact p = 0.58 (dashboard results page, `results/bench/m7/summary.json`).
- **Scenario 6 row:** agent-gemini 2 of 3, runbook 0 of 3.
- **The incident:** entry 10, run `06-iam-regression-20261001T003124Z`. The permission removed is orders' `sqs:SendMessage` on `nightshift-placed-orders`. `nightshift-orders-errors` paged 122 seconds after the injection ("two minutes later").
- **What the agent did:** its journal, steps 3 (deployments, found nothing), 6 (recent changes), 4, 5, 11, 12 and 14 (metrics), 8 (traces), 15 (orders' logs, the result containing `AccessDenied`).
- **Sixteen seconds after the page:** the page at 122 s, the answer at 138 s after injection; `orders / iam_regression` at confidence 100.
- **Nothing automatic:** its only proposal is text for a human ("restore permissions for the orders role to call sqs:SendMessage"), no allowlisted action. The scenario accepts no action; remediation graded correct.
- **The runbook:** `insufficient_evidence`, "No runbook rule explains orders-errors without a recent deploy."
- **Recorded runs, no AWS or model calls:** every page is prerendered from `dashboard/public/replay/`, and CI fails on any route that renders per request (`dashboard/scripts/check-static.mjs`).

## Don't say

- That the agent beats the runbook. It doesn't, measurably.
- That the agent is safe because it is accurate. Its safety is the read-only role, the allowlist and the human approval; it proposed something unsafe 4 times in the pass.
- That this run was live. It is a replay of a recorded run.
