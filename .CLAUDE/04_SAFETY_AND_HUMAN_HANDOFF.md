# Safety and Human Handoff

## Central Safety Principle

Automation may perform routine execution.

Automation must not independently make consequential banking business decisions.

Examples requiring an authorized institution operator:

- closing an account
- approving a transaction
- making a policy exception
- proceeding with an irreversible or high-risk action
- resolving an ambiguous situation where intent cannot be safely determined

Unknown / unsafe states must stop and escalate rather than guess.

## Action Categories

### SAFE

Examples:

- navigate within an allowed application
- read visible account data needed for the capability
- fill a search field
- open a member record
- dismiss a known harmless popup

Automation may execute these.

### APPROVAL_REQUIRED

Examples:

- submit a transaction
- close an account
- modify critical account data
- delete an important record
- irreversible operation

Automation must pause before execution and obtain operator approval.

### BLOCKED

Examples:

- navigate to an unauthorized domain
- expose credentials / tokens
- attempt an explicitly prohibited operation
- persist prohibited sensitive information

Do not execute.

Log the policy decision and terminate / escalate safely.

## Policy Enforcement

The LLM receives the safety policy in its context, but it is not the final authority.

Flow:

```text
LLM proposes structured action
→ deterministic policy engine
→ SAFE: execute
→ APPROVAL_REQUIRED: pause / request operator approval
→ BLOCKED: reject and stop
```

Replay actions also pass through the same policy layer.

Safety is enforced in both:

- discovery
- replay

## Normalized Intent

Where possible, actions should include a normalized intent.

Examples:

```text
view_account
search_member
submit_transfer
close_account
```

Policy evaluates normalized intent plus context instead of relying only on raw UI selectors / button labels.

This allows two different applications to map:

```text
"Close Account"
"Terminate Membership"
```

to a shared semantic intent such as:

```text
close_account
```

## Layered Policy Model

Conceptual policy layers:

```text
global safety defaults
→ vendor/application policy
→ institution-specific overrides
```

Examples:

Global:
- secrets must never be persisted
- outside-domain navigation blocked
- destructive actions require approval

Institution override:
- all transfers require approval
- transfers above a threshold require approval

The take-home does not need a full policy management system, but the abstractions should allow this model.

## Sensitive Data

Runtime automation may temporarily process data required to complete the operation.

Artifacts and logs must not persist raw secrets / prohibited PII.

Use:

- parameter placeholders
- field allowlists
- redaction
- masked evidence where needed

Example:

```text
{{member_id}}
[REDACTED]
```

## Human Handoff Assumption

Assume the human is an authorized employee/operator of the bank or credit union.

The operator is allowed to use the target back-office application.

This is a design assumption for the take-home.

## Handoff Model

Handoff is asynchronous.

When safe automatic recovery is exhausted:

```text
automation stuck
→ pause live session
→ create InterventionRequest
→ persist pending request
→ authorized operator sees queue
→ operator claims request
→ operator takes control of SAME live session
→ operator resolves issue
→ operator hands control back
→ automation resumes
```

Do not terminate the browser session when intervention is created.

The requirement is control transfer of the same live session.

## InterventionRequest

Suggested fields:

```text
id
run_id
capability_id
tenant_id
current_step
reason
evidence
session_id
status
claimed_by
created_at
resolved_at
```

Suggested statuses:

```text
PENDING
CLAIMED
RESOLVED
CANCELLED
```

## Control State Machine

```text
RUNNING_AUTOMATION
→ PAUSED_WAITING_FOR_HUMAN
→ HUMAN_CONTROL
→ RESUME_REQUESTED
→ RUNNING_AUTOMATION
```

Human actions during intervention should be recorded as evidence.

## When to Escalate

Do not escalate on the first transient failure.

Escalate when:

- bounded safe recovery is exhausted
- unexpected state cannot be safely classified
- a risky action requires human approval
- the discovery model loops / becomes stuck
- the application requests information automation is not authorized to provide
- a checkpoint repeatedly fails
- an unknown dialog blocks progress
