# ADR 0001: No in-code tracing; Lambda active tracing only

Status: Accepted by the owner, 2026-09-22 (M2b step 8).

## Context

The M2b plan put tracing last, behind a go/no-go gate: add OpenTelemetry with an X-Ray UDP span exporter and an X-Ray Lambda propagator, measure init duration, and stop if it added more than 300 ms to the 712 ms baseline. The reasoning was that the X-Ray SDK is in maintenance from 2026-02-25 and unsupported from 2027-02-25, and that no scenario was known to need traces that correlated logs do not already give.

Research before writing any code (2026-09-22) found that the planned path does not exist in a small, documented form for Python:

- AWS's Python migration guide recommends the **AWS Lambda Layer for OpenTelemetry** for Lambda, loaded through `AWS_LAMBDA_EXEC_WRAPPER=/opt/otel-instrument` with Application Signals turned off. Its layer ARN contains an AWS-owned account ID, which this repo's pre-commit hook blocks, and the exec wrapper adds its own init work.
- The guide's manual Python setup exports OTLP to a collector on port 4318. A Lambda function without a collector layer has no such collector.
- The only packaged Python X-Ray UDP exporter is in `aws-opentelemetry-distro` 0.20.0, which has **62 runtime dependencies** (checked on PyPI), including gRPC and HTTP OTLP exporters and instrumentation for dozens of frameworks this project does not use.
- `opentelemetry-sdk` (3 dependencies) and `opentelemetry-propagator-aws-xray` (1) are small, but include no exporter that works inside Lambda without a collector.

Meanwhile, **Lambda active tracing is already on** for all four services (`tracing_mode = "Active"`). It costs no init time, needs no SDK, and records a segment per invocation (526 traces in September by 2026-09-22), inside the X-Ray free tier of 100,000 traces a month. It cannot show work inside the handler.

## Decision

No in-code tracing. Keep Lambda active tracing. The agent's `get_traces` tool (M5) reads the segments Lambda already records. Timing inside the handler comes from structured log lines, the way the DSQL connection is now measured (`database connected` with `token_ms` and `connect_ms`), plus the platform REPORT lines logged since `system_log_level` became INFO.

## Options considered

| Option | Why not |
|---|---|
| Minimal OpenTelemetry SDK plus a hand-written UDP exporter (about 60 lines) | Depends on the ADOT UDP wire format, which AWS does not document as a public contract. Clever and brittle. |
| `aws-opentelemetry-distro` in the shared layer | 62 dependencies including gRPC, in 128 MB functions whose cold start already had to be engineered down. Very likely fails the 300 ms gate; the dependency count alone rules it out for a codebase every line of which must be explainable. |
| AWS Lambda Layer for OpenTelemetry | Documented, but needs an exception to the account ID hook for a foreign ARN and adds an exec wrapper to every cold start. |
| **Active tracing only** | Chosen. Zero init cost, fully documented, already deployed and free. |

## Consequences

- Traces show each function invocation and its duration, not spans inside it. Where a scenario needs time inside a handler, a log line with a measured duration provides it, searchable by correlation ID.
- Lambda's active tracing samples 1 request per second plus 5% of the rest, fixed and not configurable, so not every request has a trace. Correlated logs remain the complete record; traces are a sample.
- Nothing depends on the X-Ray SDK, so its end of support on 2027-02-25 does not affect the project.
- Revisit if a scenario turns out to need spans inside a handler that a timed log line cannot provide, or if AWS publishes a small documented Python exporter for Lambda.
