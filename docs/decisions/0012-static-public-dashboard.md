# ADR 0012: The public dashboard is a static replay; live views sit behind a login

Status: Accepted by the owner, 2026-10-03 (M8 plan). Recorded 2026-10-03 (M8 step 5).

## Context

The benchmark's evidence is 180 investigations: every tool call, every result, every answer and postmortem. A reader should be able to open any incident and check a claim. The design also calls for live views (service health, a live journal, approving or rejecting a proposed action) for the owner.

Anything public is attack surface. A page that reads AWS at request time lets any visitor spend the account's API requests and table capacity, and puts server code with AWS credentials on the internet. The journals are raw tool output, and an account ID was already committed once inside one (LEARNING.md, section 17).

## Decision

- **Public pages are prerendered, once, at build time** from JSON files in `dashboard/public/replay/`. No public route runs code per request. After every build, `scripts/check-static.mjs` reads what Next.js actually prerendered and fails on any route that would render per request.
- **The public data is generated and committed.** Only `scripts/build_replay.py` writes it, from a committed benchmark pass. It drops what readers don't need, scrubs IDs, scans the output for anything shaped like an account ID, cluster ID or email and refuses to write if one remains, and produces identical bytes from identical results. A test rebuilds it and fails on any difference.
- **Untrusted text stays text.** React escapes everything it renders, and postmortems are rendered as markdown with raw HTML skipped.
- **Live views are owner-only**, behind a GitHub login allowed for one GitHub user ID, with every live route checking the session itself. Their AWS access comes from Vercel's OIDC tokens through a role trusted for the production environment only. They are built in later M8 steps, each with its own approval.
- **Hosting is Vercel's Hobby plan**, which cannot bill (over a limit, the feature pauses for 30 days), with functions pinned to `yul1`, the same AWS region as the store.

## Options considered

| Option | Why not |
|---|---|
| A public API reading results from AWS | Server code and credentials on the internet for data that never changes. |
| Read `results/` at build time | Publishes whatever is there, unreviewed and unscanned. |
| S3 and CloudFront | S3 storage has no Always Free tier on this account, and the live views need server functions with a login. |
| GitHub Pages | Static only, and the live views would need a second host. |
| **Static replay on Vercel, live views behind a login** | Chosen. |

## Consequences

- The public site cannot call AWS or a model, and CI proves it on every pull request.
- Changing what is public is a pull request that shows the new files.
- The live half widens permissions (an identity provider and a role for Vercel), so it gets its own Terraform plan and approval.
