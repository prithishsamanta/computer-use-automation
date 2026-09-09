# Error Model and Observability

## Error / Outcome Categories

Use explicit categories.

### 1. Expected Business Outcomes

These are valid business states, not system failures.

Examples:

- member not found
- account already closed
- insufficient funds
- invalid member identifier according to business rules

Return structured results.

Example:

```json
{
  "status": "business_outcome",
  "code": "MEMBER_NOT_FOUND"
}
```

### 2. Recoverable Runtime Conditions

Examples:

- slow page
- expected popup
- temporary timeout
- session warning
- delayed control enablement

Replay may:

- wait
- dismiss known-safe popup
- retry
- re-resolve target

Recovery must be bounded.

### 3. Hard / System Failures

Examples:

- invalid artifact schema
- required artifact field missing
- target cannot be located after approved fallback/recovery
- checkpoint fails after bounded retry
- unrecoverable session expiration
- permission denied
- required output cannot be extracted

Possible codes:

```text
ARTIFACT_INVALID
TARGET_NOT_FOUND
CHECKPOINT_FAILED
SESSION_EXPIRED
PERMISSION_DENIED
OUTPUT_EXTRACTION_FAILED
```

### 4. Capability Routing Outcomes

No semantic capability match is not a replay failure.

Example:

```text
NO_CAPABILITY_MATCH
→ DISCOVERY_REQUIRED
```

## Human Intervention Is Not an Error Type

Human intervention is an escalation response.

It may be triggered by:

- unrecoverable failure
- unsafe state
- approval requirement
- unknown condition

Keep the triggering cause separate from the intervention state.

## Structured Logging

Every run receives a `run_id`.

Log meaningful system events across components.

Examples:

```text
run_started
capability_search_started
capability_match_found
discovery_started
llm_action_proposed
policy_check_passed
ui_action_executed
checkpoint_passed
checkpoint_failed
recovery_attempted
intervention_requested
human_control_claimed
human_control_released
output_extracted
run_completed
run_failed
```

Suggested event shape:

```json
{
  "timestamp": "...",
  "run_id": "...",
  "component": "replay_engine",
  "event": "checkpoint_failed",
  "step_id": "open_accounts",
  "status": "failure",
  "details": "Expected Accounts panel was not visible"
}
```

## Logging Principle

Log every meaningful system event.

Do not indiscriminately dump:

- complete prompts
- raw page data
- credentials
- full PII
- every implementation-level debug variable

Observability must not become a data-leak mechanism.

## Rich Failure Evidence

At least one richer failure signal should be captured.

Prefer:

- screenshot
- DOM snapshot or accessibility snapshot
- current URL / route
- current step
- error classification
- recent action history

Failure evidence should be associated with the run and step.

## Evidence Folder

The submission should contain evidence demonstrating the vertical slice.

Recommended:

```text
evidence/
  discovery.jsonl
  replay-success.jsonl
  replay-business-outcome.jsonl
  replay-failure.jsonl
  failure-screenshot.png
  artifact.json
```

Exact filenames may vary.

## Traceability

A reviewer should be able to answer:

- what request started the run?
- what capability/artifact was selected?
- what version was used?
- what action was attempted?
- what policy decision occurred?
- what checkpoint failed?
- what recovery was attempted?
- why was a human requested?
- what final result occurred?
