# Surface Abstraction and Multi-Tenant Reuse

## Surface Abstraction

Artifacts should describe logical actions and targets.

They should not directly depend on Playwright APIs.

The replay engine depends on a `SurfaceAdapter`.

Conceptual interface:

```text
observe()
click(target)
fill(target, value)
read(target)
wait_for(condition)
capture_evidence()
```

Concrete implementations may include:

```text
PlaywrightSurfaceAdapter
LegacyWebSurfaceAdapter
DesktopSurfaceAdapter
```

For the take-home, only the web implementation needs to be real.

Desktop support may remain design-only.

## Design Pattern

Use a Strategy-style abstraction with Adapter implementations.

`ReplayEngine` depends on the `SurfaceAdapter` abstraction.

At runtime, a concrete strategy performs the actual UI operations.

```text
             ReplayEngine
                  |
                  v
            SurfaceAdapter
          /        |        \
         /         |         \
Playwright     LegacyWeb    Desktop
 Adapter        Adapter      Adapter
```

This supports:

- abstraction
- polymorphism
- Open/Closed Principle
- Dependency Inversion
- future addition of new UI technologies

The names can be refined during coding, but the dependency direction should remain.

## Legacy Web Support

A legacy web application may still use Playwright.

The difference is mostly in observation and target resolution.

Potential challenges:

- frames / iframes
- nested tables
- weak / absent accessibility semantics
- generated IDs
- inconsistent labels
- structural navigation
- old JavaScript interactions

The adapter / locator resolver should support stronger fallbacks without contaminating replay domain logic with Playwright-specific details.

## Multi-Tenant Model

Same vendor application may be deployed at many institutions with small differences.

Model:

```text
Vendor / Application
→ Application Version
→ Institution / Tenant Configuration
```

A capability should primarily belong to a vendor/application/version family.

Avoid creating a completely separate artifact for every institution if the logical workflow is identical.

## Base Artifact + Overrides

Example:

```text
Base capability:
Search member
→ Open member
→ Open accounts
→ Read savings balance
```

Tenant/version differences should be represented as overrides where possible.

Example:

```text
Base locator:
"Member Search"

Version override:
"Member Lookup"

Tenant override:
target inside frame "customerFrame"
```

Conceptual resolution:

```text
base artifact
+ version override
+ tenant override
= resolved artifact for replay
```

Do not implement a very complex inheritance engine unless the demo needs it.

A simpler practical implementation can still preserve this conceptual model.

## Capability Retrieval

Do not run semantic similarity across every artifact in the system without application context.

Recommended flow:

```text
request
→ tenant/app context
→ filter to compatible vendor/application/version
→ semantic similarity over compatible capabilities
→ top candidates
→ validate candidate intent/input/output/risk compatibility
→ select artifact or DISCOVERY_REQUIRED
```

## Embeddings

Embeddings represent capability meaning.

Embed:

- capability name
- description
- purpose
- maybe normalized business intent

Do NOT embed raw click/fill step sequences as the primary semantic representation.

Metadata answers:

> Can this capability run on this application / version / tenant?

Embedding similarity answers:

> Does this capability mean the same thing as the requested task?

Both are required.

## Retrieval Threshold

Use configurable top-k retrieval and a threshold.

Do not treat a similarity score as sufficient proof.

Validate:

- application compatibility
- intent
- required inputs
- expected outputs
- safety / risk type

If no valid match exists:

```text
DISCOVERY_REQUIRED
```

The exact threshold should be tuned during implementation rather than treated as universal.

## Drift Handling

A workflow can drift for one tenant without changing globally.

Example:

```text
base artifact works for many institutions
→ Tenant X checkpoint starts failing
→ record evidence
→ identify tenant/version drift
→ create an override or new artifact version
```

Do not immediately modify the shared base artifact in response to a single-tenant failure.
