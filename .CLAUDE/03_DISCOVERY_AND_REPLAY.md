# Discovery and Deterministic Replay

## Discovery Loop

Discovery uses an LLM as the planner.

Conceptually:

```text
observe current UI
→ reason about next action
→ propose structured action
→ policy check
→ execute through SurfaceAdapter
→ observe resulting UI
→ repeat
```

Observation should happen after important actions, not only after full-page navigation.

Modern and legacy UIs can change state without navigating.

## LLM Responsibilities

The LLM may:

- interpret the current UI
- choose the next navigation / interaction step
- identify likely targets
- decide how to progress toward the requested goal
- detect that it is stuck
- propose a structured action

The LLM is NOT the final authority for:

- policy
- authorization
- risky / irreversible decisions
- whether sensitive information may be persisted

All proposed actions pass through deterministic policy checks before execution.

## Discovery Mistakes

The model can make wrong turns.

Example:

```text
A → X → B → back → Y → C → success
```

Persist:

- full discovery trace: everything tried
- reusable artifact: cleaned successful workflow only

Do not replay failed detours.

Artifact construction should derive a validated minimal successful path rather than simply serializing the entire model conversation.

## Deterministic Replay

Replay does not ask the LLM what to do next.

It follows the artifact.

Conceptual flow:

```text
load artifact
→ schema validation
→ input validation
→ for each step:
    policy check
    resolve target
    wait for target/state
    execute action
    detect known business outcomes
    detect runtime errors
    verify checkpoint where defined
→ extract typed outputs
→ verify final success condition
→ return structured result
```

## Locator Resolution

Initial preferred order:

1. accessibility role + name
2. stable semantic attributes
3. label-based targeting
4. text + context
5. structural relationships
6. frames / tables
7. CSS/XPath fallback
8. visual / coordinate fallback only if necessary

Avoid selectors such as:

```text
div:nth-child(4) > table > tr:nth-child(2)
```

unless there is genuinely no more stable option.

Each target may contain a primary locator plus approved fallback candidates.

Fallbacks should be deterministic and bounded.

## Waits and Timeouts

Avoid fixed sleeps such as:

```python
sleep(5)
```

Prefer condition-based waits:

- element visible
- element enabled
- expected text appears
- navigation completed
- expected panel present
- network / loading state settled where appropriate

Timeouts should be configurable.

Retries should be bounded.

A repeated failure should not become an infinite loop.

## Checkpoints

Checkpoints validate expected application state after meaningful transitions.

Example:

```text
click Search
→ checkpoint: member detail header visible
```

A checkpoint verifies semantic progress rather than merely verifying that the click API returned.

## Output Extraction

Outputs are declared by the artifact.

For every output:

- resolve source
- read value
- parse / normalize value
- validate declared type
- validate required constraints

Example:

```text
"$1,234.56"
→ normalize
→ Decimal("1234.56")
```

If output extraction fails:

```text
OUTPUT_EXTRACTION_FAILED
```

Do not report success.

## Replay Recovery

When a replay step fails:

1. classify failure
2. attempt only safe, bounded recovery
3. retry the step if appropriate
4. re-check checkpoint
5. if still unsafe/unresolved, escalate

Do not ask the LLM to improvise during ordinary deterministic replay.

A future bounded LLM fallback could be a stretch feature, but it is not part of the initial core.
