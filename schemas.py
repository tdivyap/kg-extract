"""
schemas.py — Pydantic models for the typed knowledge graph.

This is the in-code implementation of the methodology document's structured-
extraction stage.

Two things matter most here:

1. The TYPE and PREDICATE vocabularies are FROZEN. Once extraction starts using
   them, do not add new values casually — every new predicate fragments relation
   queries. See SCHEMA.md in the repo root for the rules around evolving this.

2. Field(description=...) is NOT documentation. When you hand a Pydantic model
   to Instructor, those descriptions become part of the JSON schema sent to the
   LLM. The model literally reads them. Write them as instructions to the model.
"""

from pydantic import BaseModel, Field
from typing import List, Literal, Optional
from enum import Enum

# Bump this whenever EntityType, Predicate, or any field semantics change.
# Old data tagged with an earlier version keeps its meaning; new extractions
# get this version stamped into their provenance. See SCHEMA.md for the rules.
SCHEMA_VERSION = "1.0.0"


# ──────────────────────────────────────────────────────────────────────────────
# CONTROLLED VOCABULARIES
# ──────────────────────────────────────────────────────────────────────────────
# `str, Enum` means these serialize as readable strings in JSON (clean
# graph_raw.json, clean Cypher queries later). The LLM is constrained to these
# values; Instructor rejects and retries any output using something else.

class EntityType(str, Enum):
    """Node types in the graph.

    Component    — a named software/hardware unit
    Mechanism    — a process or behavior (failover, retry, heartbeat)
    Parameter    — a configurable knob
    FailureMode  — a way something can go wrong (partition, isolation)
    Concept      — a non-component idea referenced by others
    Actor        — a human or external system (administrator, client)
    """
    COMPONENT = "Component"
    MECHANISM = "Mechanism"
    PARAMETER = "Parameter"
    FAILURE_MODE = "FailureMode"
    CONCEPT = "Concept"
    ACTOR = "Actor"


class Predicate(str, Enum):
    """Edge types. See SCHEMA.md for what each one means and what it does NOT.

    Add new ones only after deliberate review — every new predicate
    fragments relation queries.
    """
    DEPENDS_ON = "depends-on"
    TRIGGERS = "triggers"
    MONITORS = "monitors"
    CONFIGURES = "configures"
    REPLACES = "replaces"
    CONFLICTS_WITH = "conflicts-with"
    FALLS_BACK_TO = "falls-back-to"
    CONSTRAINS = "constrains"
    GUARANTEES_RESOURCES_FOR = "guarantees-resources-for"
    PERFORMS = "performs"
    ENABLES = "enables"
    PROTECTS = "protects"


# ──────────────────────────────────────────────────────────────────────────────
# PROVENANCE — the single most important field in the whole system
# ──────────────────────────────────────────────────────────────────────────────
# Every fact carries provenance back to the source section that justified it.
# This makes the graph auditable AND enables change propagation when source
# documents update. Without provenance you cannot resolve conflicts and cannot
# maintain the graph at scale.

class Provenance(BaseModel):
    """Where a fact came from, and under which schema version."""
    source_id: str = Field(
        description="ID from the corpus register, e.g. 'reference_doc'"
    )
    section: str = Field(
        description="Section identifier — heading text or section number"
    )
    page: Optional[int] = Field(
        default=None, description="Page number if known"
    )
    version: str = Field(
        description="Product/document version this fact applies to. "
                    "Critical for resolving version conflicts across sources."
    )
    # Stamped automatically — the LLM should not be asked to emit this.
    # Provided here so the JSON schema documents it; we set the default to
    # the current SCHEMA_VERSION and overwrite on every extraction.
    schema_version: str = Field(
        default=SCHEMA_VERSION,
        description="Graph schema version under which this fact was extracted. "
                    "Do not modify — the pipeline manages this field."
    )


# ──────────────────────────────────────────────────────────────────────────────
# ENTITIES — the nodes of the graph
# ──────────────────────────────────────────────────────────────────────────────

class Entity(BaseModel):
    """One typed node in the knowledge graph.

    The `id` uses a stable convention (comp.X, mech.Y, etc.) so entity
    resolution / deduplication has a deterministic target. Aliases like
    'X service', 'X module', 'X manager' all collapse onto comp.x.
    """
    id: str = Field(
        description="Stable ID. Convention: comp.<name>, mech.<name>, "
                    "param.<name>, fm.<name>, concept.<name>, actor.<name>. "
                    "Always snake_case."
    )
    type: EntityType
    name: str = Field(description="Canonical human-readable name")
    aliases: List[str] = Field(
        default_factory=list,
        description="Other names this entity is called in the corpus"
    )
    description: str = Field(
        description="One or two sentences. PARAPHRASED — do not quote source."
    )
    provenance: List[Provenance] = Field(
        description="Every section that mentions this entity."
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="How confident the extractor is. Lower this for "
                    "implied or inferred entities."
    )


# ──────────────────────────────────────────────────────────────────────────────
# RELATIONS — the edges, where the real value lives
# ──────────────────────────────────────────────────────────────────────────────
# The thing RAG could never give you. Each relation is a first-class object
# you can query: "find all DEPENDS_ON edges where subject = comp.X".
# That query is what vanilla RAG cannot answer reliably.

class Relation(BaseModel):
    """One typed edge between two entities."""
    id: str = Field(description="Unique relation ID, e.g. 'rel.0001'")
    subject: str = Field(description="Entity ID — the 'from' side")
    predicate: Predicate
    object: str = Field(description="Entity ID — the 'to' side")
    modality: Literal["hard", "soft"] = Field(
        description="'hard' = strict rule stated in source; "
                    "'soft' = typical/usually true"
    )
    description: str = Field(
        description="Short paraphrased explanation of the edge"
    )
    provenance: List[Provenance]
    confidence: float = Field(ge=0.0, le=1.0)


# ──────────────────────────────────────────────────────────────────────────────
# CONSTRAINTS — invariants that must hold
# ──────────────────────────────────────────────────────────────────────────────
# Example: "Exactly one master agent exists per cluster partition."
# In a later stage these can become property-based tests.

class Constraint(BaseModel):
    """An invariant or rule asserted by the source."""
    id: str
    statement: str = Field(
        description="The invariant as a single declarative sentence. "
                    "Should be verifiable — you could write a test for it."
    )
    applies_to: List[str] = Field(
        description="Entity IDs this constraint binds"
    )
    provenance: List[Provenance]


# ──────────────────────────────────────────────────────────────────────────────
# PARAMETERS — configurable knobs
# ──────────────────────────────────────────────────────────────────────────────
# Parameters descend straight into a config-schema later. Every configurable
# setting with its range and default IS a config schema field.

class Parameter(BaseModel):
    """A configurable knob with its default and effect."""
    id: str = Field(description="Convention: param.<exact_name_lower>")
    name: str = Field(
        description="Exact parameter name as used in the product"
    )
    default: str = Field(
        description="Default value as a string (preserves '2s', '30%', etc.)"
    )
    range: Optional[str] = Field(
        default=None, description="Allowed range, e.g. '2-5'"
    )
    unit: Optional[str] = Field(
        default=None, description="e.g. 'seconds', 'percent', 'count'"
    )
    effect: str = Field(
        description="One sentence on what this parameter changes"
    )
    configures: List[str] = Field(
        description="Entity IDs this parameter affects (usually Mechanisms)"
    )
    provenance: List[Provenance]


# ──────────────────────────────────────────────────────────────────────────────
# THE WRAPPER — what one extraction call returns
# ──────────────────────────────────────────────────────────────────────────────
# Bundling all four into one payload means each LLM call returns everything
# from one section in one round-trip. Instructor validates this whole object;
# if any nested field fails (wrong predicate, missing provenance) the model is
# asked to retry with the validation error as feedback. That self-correction
# is most of why this approach works at all.

class ExtractionPayload(BaseModel):
    """Everything extracted from one section of one document."""
    entities: List[Entity] = Field(default_factory=list)
    relations: List[Relation] = Field(default_factory=list)
    constraints: List[Constraint] = Field(default_factory=list)
    parameters: List[Parameter] = Field(default_factory=list)
