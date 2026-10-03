# ADR 0006: Internal calls use the Lambda Invoke API, not function URLs

Status: Accepted by the owner, 2026-09-21 (M2a step 6). Recorded as an ADR 2026-10-03 (M8).

## Context

orders-service calls cart-service to read a cart, and fulfillment calls the payment provider. The first design called cart through its function URL with `AWS_IAM` auth, signing the request with orders' execution role.

Every such request returned 403, and cart's code never ran. An identity policy, a resource policy, `lambda:*` on everything and several variations all failed. The identical request signed by an IAM user from the laptop succeeded. The pattern held for the CI role too: a request signed by an IAM role was rejected at these function URLs, and one signed by an IAM user was not. It was never root-caused.

Twice something looked fixed because a test passed seconds after a change. Both passes came from cached authorization decisions or from a deploy replacing warm environments, and one wrong conclusion was committed and broke checkout.

## Decision

- Service-to-service calls use the Lambda Invoke API on the callee's `live` alias. `service_client.call` sends an event shaped like a function URL request, so every service keeps one handler for both entry points, and sets a read timeout so a slow dependency surfaces as a timeout.
- Function URLs with `AWS_IAM` auth remain the external entry point.
- Permissions are verified with `aws iam simulate-principal-policy`, or by waiting longer than the cache, never by "it worked a minute after the change" (a project rule since then).

## Options considered

| Option | Why not |
|---|---|
| Keep debugging the function URL 403 | Days without progress, and every quick result was unreliable because of caching. |
| Function URLs with no auth | A public endpoint into the store. |
| **Invoke API** | Chosen. An internal call has no reason to leave AWS's control plane, and Invoke authorization is ordinary IAM. |

## Consequences

- Internal calls are authorized by `lambda:InvokeFunction` on exact alias ARNs, which the policy simulator can check.
- A slow-dependency scenario surfaces as a caller's timeout, which is exactly what scenarios 3 and 4 test.
- The unexplained 403 is recorded as unexplained. The rule it produced, that a fast negative or positive result about IAM is not evidence, shaped every later permission change.
