# Automation Artifact Schema

## Purpose

An artifact is the reusable deterministic representation produced after a successful discovery run.

The artifact must be:

- typed
- parameterized
- versioned
- reviewable
- decoupled from the raw LLM transcript
- safe to execute without an LLM making runtime decisions

The discovery trace and reusable artifact are different things.

A discovery trace may contain failed attempts, detours, retries, and model reasoning.

The artifact should contain only the cleaned and validated reusable workflow.

## Recommended Top-Level Shape

```json
{
  "capability_id": "get_savings_balance",
  "name": "Get Savings Balance",
  "description": "Find a member and return the current savings balance.",
  "version": "1.0.0",

  "application": {
    "vendor": "demo-core",
    "application": "credit-union-admin",
    "supported_versions": ["1.x"]
  },

  "tenant_scope": "base",

  "inputs": {},
  "outputs": {},

  "steps": [],

  "success_condition": {},

  "business_outcomes": [],
  "recoverable_conditions": [],

  "safety": {},

  "provenance": {
    "created_from_run": "run-id",
    "created_at": "timestamp"
  }
}
```

Exact field names may change while coding, but these concepts should remain.

## Inputs

Inputs are typed and parameterized.

Example:

```json
{
  "inputs": {
    "member_id": {
      "type": "string",
      "required": true,
      "description": "Institution member identifier"
    }
  }
}
```

Artifacts should contain placeholders such as:

```text
{{member_id}}
```

Do not persist invocation-specific PII or secrets in the artifact.

## Outputs

Outputs are typed and include extraction instructions.

Example:

```json
{
  "outputs": {
    "savings_balance": {
      "type": "decimal",
      "source": {
        "strategy": "label_relative",
        "label": "Savings Balance"
      },
      "required": true
    }
  }
}
```

Output extraction must be validated.

If a decimal is expected and the UI returns an unexpected string, replay must not silently report success.

## Steps

Each step should describe:

- unique step id
- action
- target
- input/value if needed
- timeout/wait rule
- optional checkpoint
- normalized intent
- safety/risk metadata

Example:

```json
{
  "id": "search_member",
  "action": "fill",
  "intent": "enter_member_id",
  "target": {
    "primary": {
      "strategy": "role_name",
      "role": "textbox",
      "name": "Member ID"
    },
    "fallbacks": []
  },
  "value": "{{member_id}}",
  "wait": {
    "timeout_ms": 5000
  },
  "checkpoint": null,
  "risk": "safe"
}
```

## Target / Locator Definition

A target should describe semantic locator candidates rather than embedding Playwright-specific code directly into the artifact.

Preferred conceptual hierarchy:

1. accessibility role + accessible name
2. stable semantic attributes / names
3. associated label
4. visible text + surrounding context
5. frame / table / structural relationship
6. brittle CSS/XPath only when required
7. visual / coordinate targeting only as a last resort

The exact locator representation may evolve during implementation.

## Checkpoints

Do not necessarily add a checkpoint after every tiny action.

Add checkpoints after meaningful state transitions.

Examples:

```text
Submit member search
→ member details visible

Open accounts
→ accounts panel visible

Read balance
→ valid savings balance extracted
```

A checkpoint failure must stop normal progression and enter recovery/error handling.

## Success Condition

The artifact has an explicit final success condition.

Example:

```json
{
  "success_condition": {
    "type": "output_valid",
    "output": "savings_balance"
  }
}
```

Success should never mean only "all clicks executed without throwing an exception."

The resulting application state / output must be verified.

## Business Outcomes

Known business states are not system failures.

Examples:

```json
{
  "business_outcomes": [
    {
      "code": "MEMBER_NOT_FOUND",
      "detect": {
        "type": "text_present",
        "value": "Member not found"
      }
    }
  ]
}
```

## Recoverable Conditions

Known transient runtime states can be represented separately.

Examples:

- delayed page load
- expected session warning
- known harmless popup
- temporarily disabled control

Recovery should be bounded and safe.

## Safety Metadata

The artifact should contain normalized intent / risk metadata where appropriate.

Example:

```json
{
  "intent": "close_account",
  "risk": "approval_required"
}
```

The artifact metadata is not itself the final safety authority.

The runtime policy engine must still evaluate every action because policy can vary by tenant and can change after artifact creation.

## Versioning

Artifacts are versioned and reviewable.

Changes such as:

- locator updates
- output extraction changes
- workflow changes
- new checkpoints
- tenant/version overrides

should create a new artifact version or override rather than silently mutating the old definition.
