# NightShift dashboard

The public replay of the benchmark: results, every incident, and every investigation step by step. Next.js and TypeScript, deployed on Vercel's free Hobby plan.

Every page is prerendered at build time from the JSON files in `public/replay/`, which `scripts/build_replay.py` writes from the committed results in `../results/`. Nothing on the public site runs per request, so no visitor can cause an AWS call or a model call. CI checks that after every build (`npm run check:static`).

## Run it locally

Node 24 inside WSL (not the Windows npm on the PATH):

```bash
npm ci
npm run dev          # http://localhost:3000
```

The same checks CI runs:

```bash
npm run lint && npm run typecheck && npm test && npm run build && npm run check:static
```

## Changing the data

Never edit `public/replay/` by hand. Rebuild it from the results, from the repository root:

```bash
python scripts/build_replay.py           # rewrite
python scripts/build_replay.py --check   # exit 1 if the committed copy differs
```

The Python tests fail if the committed files differ from what the results build, or if any file contains something that looks like an account ID, a cluster ID or an email address.
