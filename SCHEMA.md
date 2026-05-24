# Schema reference

This document defines the typed vocabulary used by `schemas.py` and the rules
for changing it. The schema is a contract. The whole pipeline depends on its
stability.

**Current version:** `1.0.0` (see `SCHEMA_VERSION` in `schemas.py`)

---

## Why a frozen vocabulary

Every new predicate fragments your relation queries. If `depends-on`,
`relies-on`, `requires`, and `needs` all exist, a user asking "what does X
depend on" now has to also ask "what does X rely on, require, or need."
Most relationships in technical documentation can be expressed with a small,
deliberately constrained vocabulary — the discipline is in saying no to
new predicates unless they pay for their own complexity.

When you find yourself wanting to add a predicate, **first ask whether an
existing one carries the meaning with a richer `description` and `modality`**.
80% of the time, yes.

---

## Entity types

Six types, frozen for v1.0.

| Type           | Meaning                                                                |
| -------------- | ---------------------------------------------------------------------- |
| `Component`    | A named software or hardware unit. Has lifecycle, has identity.        |
| `Mechanism`    | A process or behavior. Acts on or among components.                    |
| `Parameter`    | A configurable knob. Has a default, a range, and an effect.            |
| `FailureMode`  | A way the system can break. Network partition, host isolation, etc.    |
| `Concept`      | A non-component idea referenced by components. Role, state, policy.    |
| `Actor`        | A human or external system that interacts with the documented system.  |

**Not included** (and the reasoning):

- *Event* — collapses into `Mechanism` with appropriate description.
  Adding a separate Event type fragments queries.
- *State* — collapses into `Concept`.
- *Module / Service / Process* — all collapse into `Component`. The
  distinction is rarely useful at the graph level.

---

## Predicates

Twelve predicates, frozen for v1.0. Each has a precise meaning and is
distinct from every other.

| Predicate                  | Meaning                                                     | Example                                                          |
| -------------------------- | ----------------------------------------------------------- | ---------------------------------------------------------------- |
| `depends-on`               | Subject requires object to function correctly.              | `comp.scheduler --[depends-on]--> comp.reservation_mgr`           |
| `triggers`                 | Subject causes object to occur or activate.                 | `fm.partition --[triggers]--> mech.failover`                      |
| `monitors`                 | Subject observes object's state.                            | `mech.heartbeat --[monitors]--> comp.host`                        |
| `configures`               | Subject (typically Parameter) sets behavior of object.      | `param.timeout --[configures]--> mech.heartbeat`                  |
| `replaces`                 | Subject supersedes object (version transitions).            | `comp.v2 --[replaces]--> comp.v1`                                 |
| `conflicts-with`           | Subject and object are mutually incompatible.               | `param.a --[conflicts-with]--> param.b`                           |
| `falls-back-to`            | Subject degrades to object on failure.                      | `mech.primary --[falls-back-to]--> mech.secondary`                |
| `constrains`               | Subject limits object's range of values or behaviors.       | `concept.policy --[constrains]--> mech.scheduling`                |
| `guarantees-resources-for` | Subject reserves capacity that object consumes.             | `comp.reservation_mgr --[guarantees-resources-for]--> mech.restart` |
| `performs`                 | Subject (typically Component) executes object (Mechanism).  | `comp.scheduler --[performs]--> mech.resource_balance`            |
| `enables`                  | Subject's existence allows object to happen.                | `mech.defrag --[enables]--> mech.failover`                        |
| `protects`                 | Subject prevents harm to object.                            | `mech.reservation_mgr --[protects]--> comp.cluster`               |

### What is deliberately NOT a predicate

These have been considered and rejected. The reasoning matters.

- **`causes` / `caused-by`** — too general. Use `triggers` (proximate cause)
  or `depends-on` (logical prerequisite) instead.
- **`uses` / `used-by`** — too general. Use `depends-on` for required usage
  or `performs` for procedural usage.
- **`contains` / `is-part-of`** — composition. Out of scope for v1.0. Most
  technical documentation describes interaction, not composition; introduce
  these only if a use case demands them.
- **`communicates-with`** — too symmetric and too vague. Use `triggers`,
  `monitors`, or `depends-on` with appropriate modality instead.
- **`relates-to`** — meaningless. The whole point of a typed graph is to
  refuse this kind of edge.

---

## Modality

Every relation carries a `modality` field with two possible values:

| Value   | Meaning                                                         |
| ------- | --------------------------------------------------------------- |
| `hard`  | The source states this as a strict, always-true rule.           |
| `soft`  | The source describes this as typical, usually true, or default. |

`hard` and `soft` edges can coexist between the same pair of entities under
different predicates. Query consumers should respect modality when reasoning
about invariants vs. defaults.

---

## Schema evolution

The `schema_version` field in `Provenance` tags every fact with the version
under which it was extracted. This makes the following workflows tractable.

### Case 1 — additive change (CHEAP)

Adding a new entity type, predicate, or optional field. **Backwards-compatible.**

1. Add the new value or field to `schemas.py`
2. Bump `SCHEMA_VERSION` to a new minor version (e.g. `1.0.0` → `1.1.0`)
3. Update this document
4. New extractions use the new value; old extractions remain valid

No re-extraction needed. Old data is untouched.

### Case 2 — breaking change (EXPENSIVE)

Renaming a predicate, removing a type, changing the semantics of an existing
field. **Old data becomes ambiguous.**

1. Bump `SCHEMA_VERSION` to a new major version (e.g. `1.x.x` → `2.0.0`)
2. Document the change in this file with before/after examples
3. Re-extract every source affected by the change
4. During the migration window, queries should filter by `schema_version`
   to avoid mixing semantics across versions
5. Once migration is complete, tag a release and delete the migration code

### Case 3 — not a schema change (MOST COMMON)

You found a new kind of relationship in a source document and want to model
it. **Default response: don't change the schema.** First try:

- Can it be expressed with an existing predicate and a richer `description`?
- Can it use `modality: soft` to capture nuance?
- Is the new predicate going to appear more than ~5 times across the corpus?
  If not, it's not worth the cost.

Only after answering these honestly and concluding that no existing predicate
fits should you propose an addition. Document the reasoning in a PR.

---

## When in doubt

Open an issue. Schema decisions outlive any single contributor, and a
five-minute discussion before adding a predicate is cheaper than an afternoon
migration after one ships.
