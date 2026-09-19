"""Phase 16_2 — the Local-Llama (Ollama) Stage Driver.

``OllamaStageDriver`` walks the prompt-to-world pipeline with STRUCTURED
per-stage LLM calls over the SAME ``GenerateRequest`` / ``ProviderResult``
boundary (``OllamaProvider`` is the transport; nothing Ollama-specific leaks
out of that adapter):

    CASE/PEOPLE (CASE_TRUTH stage surface)
      -> EVIDENCE (EVIDENCE stage surface)
      -> WORLD_REQUIREMENTS (WORLD_REQUIREMENTS stage surface)
      -> Environment Resolver -> Asset Oracle -> World Composer
      -> ASSET_SPEC (only for unknown/REQUIRED objects the Oracle cannot
         resolve) with a bounded ASSET_SPEC_REPAIR loop

The driver PROPOSES structured facts; it NEVER decides who the murderer is or
whether a case is solvable. It assembles a normal ``GeneratedDraft`` so the
EXISTING deterministic validation suite (safety, solver, world, truth,
publication gate) runs unchanged, and it is invoked from inside the EXISTING
``GenerationController`` lifecycle (budget, repair/regenerate classification,
publication CAS) — no parallel lifecycle is introduced.

The driver's outcome when any stage yields terminal-unrepairable output is
classified by the existing controller: a provider-level failure
(``StageDriverProviderFailure``) fails the attempt; a stage parse/validation
failure leaves ``attempt.deferred_structural`` populated so
``pipeline.validate_draft`` classifies RECOVERABLE_REPAIR / REGENERATE /
TERMINAL exactly as today.

``OllamaAssetSpecProvider`` adapts the narrow ``AssetSpecProvider`` (used by
``app.world.composer.compose_world``) to the Ollama provider: an ASSET_SPEC
call followed by at most ``MAX_SPEC_REPAIR_PASSES`` ASSET_SPEC_REPAIR calls
each re-parzed and re-validated with the strict Phase 13 validator before being
returned. All calls consume the per-attempt provider-call budget.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from app.generation.provider import (
    GenerateRequest,
    GenerationStage,
    ProviderResult,
)
from app.generation import prompts
from app.generation import parser as stage_parser
from app.world.requirements import (
    CRITICALITY_DECORATIVE,
    CRITICALITY_REQUIRED,
    ObjectRequest,
    PlacementRelation,
    RELATION_KINDS,
    WorldRequirements,
)

# Bounded AssetSpec repair passes INSIDE one AssetSpec round-trip (Phase16_2
# §13: "bounded ≤2 per driver").
MAX_SPEC_REPAIR_PASSES = 2

# The four (non-AssetSpec) stage surfaces the driver walks (in order).
_DRIVER_STAGES = (
    GenerationStage.CASE_TRUTH,
    GenerationStage.EVIDENCE,
    GenerationStage.WORLD_GRAPH,
)


class StageDriverProviderFailure(Exception):
    """A provider-level failure inside the stage driver (terminal for the
    attempt, sanitized by the controller)."""


class _DuplicateKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(key)
        result[key] = value
    return result


def _parse_doc(content: str) -> dict[str, Any]:
    """Strict single-document parse with duplicate-key rejection (mirror of the
    strict generation parser) — returns the dict or raises ``ValueError``."""
    if not isinstance(content, str) or not content.strip():
        raise ValueError("provider output is empty")
    return json.loads(content, object_pairs_hook=_reject_duplicate_keys)


# --------------------------------------------------------------------------- #
# CASE/PEOPLE + WORLD_REQUIREMENTS strict parsing (reuses the authoritative
# stage parsers where a section matches one; never a lenient analogue).
# --------------------------------------------------------------------------- #


def parse_case_people(content: str) -> tuple[Any, dict[str, Any]]:
    """Strict-parse a CASE/PEOPLE stage response.

    Returns ``(crime_spec, public_world_spec)`` where ``crime_spec`` is a
    ``CrimeSpec`` and ``public_world_spec`` is a ``PublicWorldSpec`` (built by
    the authoritative PUBLIC_WORLD parser from the persons/motives/locations/
    travelRules/scene sections). NEVER coerces; raises ``ValueError`` carrying
    the parsed structure on success only when the structure is valid.
    """
    data = _parse_doc(content)
    if not isinstance(data, dict):
        raise ValueError("case_people root must be a JSON object")
    extra = set(data) - {"crime", "persons", "motives", "locations", "travelRules", "scene"}
    if extra:
        raise ValueError(f"case_people: unknown keys {sorted(extra)!r}")
    if "crime" not in data:
        raise ValueError("case_people: missing required key 'crime'")
    crime = stage_parser.parse_stage(
        GenerationStage.CASE_TRUTH,
        json.dumps({"crime": data["crime"]}, sort_keys=True),
        non_throwing=False,
    )
    public_payload = {key: val for key, val in data.items() if key in (
        "persons", "motives", "locations", "travelRules", "scene"
    )}
    public_payload["objects"] = public_payload.get("objects", [])
    public = stage_parser.parse_stage(
        GenerationStage.PUBLIC_WORLD,
        json.dumps(public_payload, sort_keys=True),
        non_throwing=False,
    )
    return crime, public


def parse_world_requirements(content: str) -> WorldRequirements:
    """Strict-parse a WORLD_REQUIREMENTS stage response into the typed
    ``WorldRequirements`` (bounded ObjectRequests / PlacementRelations)."""
    data = _parse_doc(content)
    if not isinstance(data, dict):
        raise ValueError("world_requirements root must be a JSON object")
    objects: list[ObjectRequest] = []
    for index, item in enumerate(data.get("objects") or ()):
        if not isinstance(item, dict):
            raise ValueError(f"world_requirements.objects[{index}] must be an object")
        name = item.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"world_requirements.objects[{index}]: name required")
        criticality = item.get("criticality")
        if criticality not in (CRITICALITY_REQUIRED, CRITICALITY_DECORATIVE):
            criticality = CRITICALITY_DECORATIVE
        try:
            objects.append(
                ObjectRequest(
                    requested_name=name,
                    category_hint=item.get("categoryHint"),
                    subtype_hint=item.get("subtypeHint"),
                    tags=tuple(item.get("tags") or ()),
                    required_interaction=item.get("requiredInteraction"),
                    evidence_id=item.get("evidenceId"),
                    criticality=criticality,
                )
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"world_requirements.objects[{index}] invalid: {exc}"
            ) from None

    relations: list[PlacementRelation] = []
    for index, item in enumerate(data.get("relations") or ()):
        if not isinstance(item, dict):
            raise ValueError(f"world_requirements.relations[{index}] must be an object")
        kind = item.get("kind")
        if kind not in RELATION_KINDS:
            raise ValueError(
                f"world_requirements.relations[{index}]: kind {kind!r} not in "
                f"{list(RELATION_KINDS)!r}"
            )
        try:
            relations.append(PlacementRelation(kind=kind, target=item.get("target") or ""))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"world_requirements.relations[{index}] invalid: {exc}"
            ) from None

    try:
        return WorldRequirements(
            environment_hint=data.get("environmentHint"),
            location_tokens=tuple(data.get("locationTokens") or ()),
            objects=tuple(objects),
            relations=tuple(sorted(set(relations), key=lambda r: (r.kind, r.target))),
            unsafe_unsupported=tuple(data.get("unsafeUnsupported") or ()),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"world_requirements invalid: {exc}") from None


# --------------------------------------------------------------------------- #
# AssetSpec adapter (implements app.assets.spec_provider.AssetSpecProvider)
# --------------------------------------------------------------------------- #


class OllamaAssetSpecProvider:
    """Bridges the world composer's narrow spec-provider to the Ollama adapter.

    One ASSET_SPEC call + at most ``MAX_SPEC_REPAIR_PASSES`` ASSET_SPEC_REPAIR
    calls. Every response is strictly Phase-13 parsed and validated BEFORE the
    Phase 17 geometry-quality validator runs (``schema-valid !=
    geometrically-valid``); each call consumes the per-attempt provider-call
    budget via a provided ``budget_consumer`` (returns True when a call is
    available).

    Phase 17 flow (Deterministic Geometry Quality Gate — Phase17 §2/§10):

        LLM AssetSpec
          -> strict Phase 13 parse/validation
          -> Geometry Quality Validator (app.assets.geometry_quality)
          -> PASS  -> raw candidate is returned for the trusted compiler
             FAIL  -> structured sanitized geometry diagnostics ->
                      ASSET_SPEC_REPAIR (bounded) -> re-run BOTH validations

    A repair result is NEVER trusted incrementally: the full Phase 13 + Phase 17
    validation run again before any candidate is accepted. The budget stays the
    existing ``MAX_SPEC_REPAIR_PASSES`` (<=2); the global per-attempt provider
    and model-call budgets are authoritative (this provider consumes them via
    ``budget_consumer`` inside ``_roundtrip``).

    Internal (non-API, non-player-facing) trace:

    - ``last_geometry_report`` — the final ``GeometryReport`` (None when the
      round-trip never reached the geometry stage with a parsed spec);
    - ``last_geometry_metrics`` — the sanitized metrics dict (see
      ``_record_geometry_metrics``): issueCountBeforeRepair, repairAttempts,
      finalPartCount, finalBoundingBox, declaredDimensions,
      silhouettePassed, generatedOnFirstPass / repaired;
    - ``last_repair_trace`` — the sanitized per-pass diagnostics list (one
      entry per validation pass, order preserved): every entry carries the
      Phase 13 structural issue strings and the Phase 17 geometry issues
      (code/classification/message/partId) for THAT pass. Never serialized,
      never served; read by the smoke CLI to report "each repair attempt's
      issues".
    """

    def __init__(
        self,
        *,
        provider: Any,
        attempt_id: str,
        budget_consumer: Callable[[], bool],
        locked: Any = None,
        seed: int | None = None,
        model_label: str = "",
    ) -> None:
        self._provider = provider
        self._attempt_id = attempt_id
        self._budget_consumer = budget_consumer
        self._locked = locked
        self._seed = seed
        self._model_label = model_label
        self.calls: int = 0
        # Phase 17 internal trace (never serialized, never served).
        self.last_geometry_report: Any = None
        self.last_geometry_metrics: dict[str, Any] | None = None
        # Phase17B: sanitized per-pass diagnostics trace (repair-path
        # diagnostics for the smoke CLI; never serialized, never served).
        self.last_repair_trace: list[dict[str, Any]] = []

    def generate(self, request: Any) -> Any:
        from app.assets.spec_provider import (
            AssetSpecRequest,
            AssetSpecResponse,
        )
        from app.assets.specs import parse_asset_spec, validate_asset_spec
        from app.assets.geometry_quality import validate_geometry

        request = (
            request if isinstance(request, AssetSpecRequest) else AssetSpecRequest(**dict(request))
        )
        self.calls += 1
        self.last_repair_trace = []
        concept = request.requested_name
        category_hint = request.category_hint or None

        # initial ASSET_SPEC call
        prompt = prompts.build_asset_spec_prompt(concept, category_hint)
        content = self._roundtrip(prompt, GenerationStage.ASSET_SPEC, concept)
        if content is None:
            return AssetSpecResponse(error="asset spec generation unavailable")

        candidate = content
        repair_attempts = 0
        issue_count_before_repair: int | None = None
        final_spec: Any = None
        final_report: Any = None

        for _pass in range(MAX_SPEC_REPAIR_PASSES + 1):
            # ---- 1. Phase 13 schema/security validation FIRST ----------------
            struct_issues = validate_asset_spec(candidate)
            geometry_report = None
            spec = None
            if not struct_issues:
                spec = parse_asset_spec(candidate)
            if spec is not None:
                # ---- 2. Phase 17 geometry-quality gate SECOND ----------------
                geometry_report = validate_geometry(spec, requested_name=concept)

            # Sanitized per-pass diagnostics (order preserved; never raw text).
            trace_entry = self._trace_entry(struct_issues, geometry_report)
            trace_entry["pass"] = _pass
            self.last_repair_trace.append(trace_entry)

            accepted = (
                not struct_issues
                and geometry_report is not None
                and geometry_report.valid
            )
            if accepted:
                if issue_count_before_repair is None:
                    issue_count_before_repair = 0
                final_spec = spec
                final_report = geometry_report
                break
            if issue_count_before_repair is None:
                issue_count_before_repair = len(struct_issues) + (
                    len(geometry_report.issues) if geometry_report is not None else 0
                )
                final_report = geometry_report  # first failing report (trace)
            if _pass >= MAX_SPEC_REPAIR_PASSES:
                break

            # bounded repair: structured sanitized diagnostics into the repair
            # prompt; the repair decides NOTHING about trust.
            diagnostics = self._repair_diagnostics(candidate, struct_issues, geometry_report)
            repair_prompt = prompts.build_asset_spec_repair_prompt(
                concept, candidate, diagnostics
            )
            content = self._roundtrip(
                repair_prompt, GenerationStage.ASSET_SPEC_REPAIR, concept
            )
            if content is None:
                self._record_geometry_metrics(
                    issue_count_before_repair, repair_attempts, final_report
                )
                return AssetSpecResponse(error="asset spec repair unavailable")
            repair_attempts += 1
            candidate = content

        if final_spec is None or final_report is None or not final_report.valid:
            self._record_geometry_metrics(
                issue_count_before_repair, repair_attempts, final_report
            )
            return AssetSpecResponse(
                error="asset spec could not be made geometrically valid within the repair budget"
            )
        self._record_geometry_metrics(
            issue_count_before_repair, repair_attempts, final_report
        )
        return AssetSpecResponse(content=candidate)

    def _repair_diagnostics(
        self, candidate: str, struct_issues: Any, geometry_report: Any
    ) -> tuple[str, ...]:
        """Sanitized structured diagnostics for one repair request.

        Includes the structured INVALID_IDENTIFIER / MATERIAL_NOT_ALLOWED
        diagnostics for the RAW candidate (Phase 13 already validates grammar and
        materials — this deterministic scan surfaces them in repair diagnostics
        too, Phase17 §5/§7) plus the geometry-quality issues when the candidate
        parsed, plus the Phase 13 structural issue strings when it did not.
        """
        from app.assets.geometry_quality import (
            GeometryIssue,
            inspect_raw_spec_issues,
        )

        lines: list[str] = []

        def _render(issue: Any) -> str:
            where = f" part {issue.partId}" if getattr(issue, "partId", None) else ""
            allowed = ""
            if getattr(issue, "allowed", ()):
                allowed = " (allowed: " + ", ".join(issue.allowed) + ")"
            return f"[{issue.classification} {issue.code}]{where}: {issue.message}{allowed}"

        raw_issues = inspect_raw_spec_issues(candidate) or ()
        for issue in raw_issues:
            lines.append(_render(issue))
        if geometry_report is not None:
            for issue in geometry_report.issues:
                lines.append(_render(issue))
        if struct_issues:
            lines.extend(str(issue) for issue in struct_issues)
        # deterministic, de-duplicated
        seen: set[str] = set()
        out: list[str] = []
        for line in lines:
            if line not in seen:
                seen.add(line)
                out.append(line)
        return tuple(out)

    def _trace_entry(
        self, struct_issues: Any, geometry_report: Any
    ) -> dict[str, Any]:
        """Sanitized per-pass diagnostics (never raw candidate text, never
        player-facing). Structural issue strings are app-owned deterministic
        messages; geometry issues carry only code/classification/message/partId.
        """
        geometry: list[dict[str, Any]] = []
        if geometry_report is not None:
            geometry = [
                {
                    "code": issue.code,
                    "classification": issue.classification,
                    "message": issue.message,
                    "partId": issue.partId,
                }
                for issue in geometry_report.issues
            ]
        return {
            "structuralIssues": [str(issue) for issue in (struct_issues or ())],
            "geometryIssues": geometry,
        }

    def _record_geometry_metrics(
        self,
        issue_count_before_repair: int | None,
        repair_attempts: int,
        final_report: Any,
    ) -> None:
        """Internal per-AssetSpec quality metrics (never player-facing, never
        serialized). Used for QA/showcase evidence and the smoke CLI output."""
        report = final_report
        if report is None:
            self.last_geometry_metrics = {
                "issueCountBeforeRepair": int(issue_count_before_repair or 0),
                "repairAttempts": int(repair_attempts),
                "finalPartCount": 0,
                "finalBoundingBox": None,
                "declaredDimensions": None,
                "silhouettePassed": False,
                "generatedOnFirstPass": False,
                "repaired": False,
            }
            return
        metrics = report.metrics
        bbox = (
            {
                "min": list(metrics.estimated_bounding_box[0]),
                "max": list(metrics.estimated_bounding_box[1]),
                "span": list(metrics.span),
            }
            if metrics.estimated_bounding_box is not None
            else None
        )
        self.last_geometry_report = report
        self.last_geometry_metrics = {
            "issueCountBeforeRepair": int(issue_count_before_repair or 0),
            "repairAttempts": int(repair_attempts),
            "finalPartCount": int(metrics.part_count),
            "finalBoundingBox": bbox,
            "declaredDimensions": list(metrics.declared_dimensions),
            "silhouettePassed": bool(metrics.silhouette_passed),
            "generatedOnFirstPass": bool(issue_count_before_repair == 0),
            "repaired": bool(repair_attempts > 0),
        }

    def _roundtrip(
        self, prompt: str, stage: GenerationStage, concept: str
    ) -> str | None:
        """One bounded budgeted Ollama call; returns raw content or None."""
        if not self._budget_consumer():
            raise StageDriverProviderFailure("model call budget exhausted")
        request = GenerateRequest(
            attempt_id=self._attempt_id,
            stage=stage,
            prompt_context=prompt,
            locked=self._locked,
            seed=self._seed,
        )
        result = self._provider.generate(request)
        if result.timed_out or result.error is not None or result.content is None:
            return None
        return result.content


# --------------------------------------------------------------------------- #
# the stage driver
# --------------------------------------------------------------------------- #


class OllamaStageDriver:
    """Walks the structured per-stage pipeline and assembles a ``GeneratedDraft``.

    ``provider_factory`` returns a FRESH ``OllamaProvider`` per attempt (the
    same factory the service uses for ollama) so transport state never leaks
    across attempts. ``oracle`` (Asset Oracle), ``spec_provider`` (an
    ``AssetSpecProvider`` for the ASSET_SPEC path — normally an
    ``OllamaAssetSpecProvider``) and ``cache`` are shared.
    """

    def __init__(
        self,
        *,
        settings: Any,
        provider_factory: Callable[[], Any],
        generated_cache: Any = None,
        catalog: Any = None,
        spec_provider: Any = None,
    ) -> None:
        from app.assets.generated_cache import GeneratedAssetCache

        self._settings = settings
        self._provider_factory = provider_factory
        # Fresh bounded generated-asset cache per driver so repeated prompt runs
        # (and tests) stay isolated and deterministic — no cross-run pollution.
        self._generated_cache = generated_cache if generated_cache is not None else GeneratedAssetCache()
        self._catalog = catalog
        self._spec_provider = spec_provider

    # -- public driver entry points (controller lifecycle) -------------------

    def run_into(self, attempt: Any, diagnostics: tuple[str, ...] = ()) -> None:
        """Fresh (or repair) staged run; sets ``attempt.draft`` on success.

        Provider-level failures raise ``StageDriverProviderFailure`` (the
        controller fails the attempt). Parse/validation failures leave
        ``attempt.deferred_structural`` populated and ``attempt.draft`` set to
        whatever assembled, so ``pipeline.validate_draft`` classifies them via
        the EXISTING controller.
        """
        from app.generation import pipeline

        deferred: list[str] = []
        provider = self._provider_factory()
        budget_consumer = self._make_budget_consumer(attempt)

        locked_map = self._locked_map(attempt)
        # --- 1. CASE/PEOPLE -------------------------------------------------
        case_prompt = prompts.build_case_people_prompt(attempt.prompt, locked_map)
        case_content = self._call(
            provider, attempt, GenerationStage.CASE_TRUTH, case_prompt, budget_consumer
        )
        crime = None
        public: dict[str, Any] | None = None
        if case_content is None:
            deferred.append("case_people stage produced no usable content")
        else:
            try:
                crime, public = parse_case_people(case_content)
            except (TypeError, ValueError) as exc:
                deferred.append(f"case_people stage parse failed: {exc}")

        # --- 2. EVIDENCE ----------------------------------------------------
        evidence_prompt = prompts.build_evidence_prompt(attempt.prompt, locked_map)
        evidence_content = self._call(
            provider, attempt, GenerationStage.EVIDENCE, evidence_prompt, budget_consumer
        )
        evidence_spec = None
        if evidence_content is None:
            deferred.append("evidence stage produced no usable content")
        else:
            try:
                evidence_spec = stage_parser.parse_stage(
                    GenerationStage.EVIDENCE, evidence_content, non_throwing=False
                )
            except (TypeError, ValueError) as exc:
                deferred.append(f"evidence stage parse failed: {exc}")

        # --- 3. WORLD_REQUIREMENTS --------------------------------------------
        world_prompt = prompts.build_world_requirements_prompt(attempt.prompt, locked_map)
        world_content = self._call(
            provider, attempt, GenerationStage.WORLD_GRAPH, world_prompt, budget_consumer
        )
        world_reqs = WorldRequirements()
        if world_content is None:
            deferred.append("world_requirements stage produced no usable content")
        else:
            try:
                world_reqs = parse_world_requirements(world_content)
            except (TypeError, ValueError) as exc:
                deferred.append(f"world_requirements stage parse failed: {exc}")

        # --- 4. world composition (Environment Resolver + Oracle + placer) ---
        spec_adapter = (
            self._spec_adapter(provider, attempt, budget_consumer)
            if self._spec_provider is None
            else self._spec_provider
        )
        composition = self._compose_world(attempt, world_reqs, spec_adapter, deferred)

        # --- 5. assemble the GeneratedDraft -----------------------------------
        attempt.deferred_structural = tuple(sorted(set(deferred)))
        attempt.draft = self._assemble(
            attempt,
            crime,
            public,
            evidence_spec,
            world_reqs,
            composition,
        )
        attempt._phase3_cache = None

    def _make_budget_consumer(self, attempt: Any) -> Callable[[], bool]:
        def _consume() -> bool:
            budget = attempt.budget
            if budget is None:
                return False
            if budget.deadline_passed():
                return False
            return budget.consume_call()

        return _consume

    def _spec_adapter(self, provider: Any, attempt: Any, budget: Callable[[], bool]) -> Any:
        return OllamaAssetSpecProvider(
            provider=provider,
            attempt_id=attempt.attempt_id,
            budget_consumer=budget,
            locked=attempt.locked,
            seed=attempt.seed,
        )

    def _call(
        self,
        provider: Any,
        attempt: Any,
        stage: GenerationStage,
        prompt: str,
        budget: Callable[[], bool],
    ) -> str | None:
        if not budget():
            raise StageDriverProviderFailure("model call budget exhausted")
        request = GenerateRequest(
            attempt_id=attempt.attempt_id,
            stage=stage,
            prompt_context=prompt,
            locked=attempt.locked,
            diagnostics=(),
            seed=attempt.seed,
        )
        result: ProviderResult = self._invoke(provider, request)
        if result.timed_out or result.error is not None or result.content is None:
            return None
        return result.content

    @staticmethod
    def _invoke(provider: Any, request: GenerateRequest) -> ProviderResult:
        result = provider.generate(request)
        if not isinstance(result, ProviderResult):
            raise StageDriverProviderFailure("provider returned an invalid result type")
        return result

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _locked_map(attempt: Any) -> Mapping[str, Any] | None:
        locked = getattr(attempt, "locked", None)
        if locked is None:
            return None
        return {key: value for key, value in locked.locked_fields() if value is not None}

    def _compose_world(
        self,
        attempt: Any,
        world_reqs: WorldRequirements,
        spec_adapter: Any,
        deferred: list[str],
    ) -> Any:
        """Environment Resolver -> Asset Oracle -> World Composer."""
        from app.environments.compose import scene_for_kit
        from app.environments.manifests import load_environment
        from app.environments.resolver import FALLBACK_ENVIRONMENT_ID
        from app.world.composer import WorldComposition, compose_world

        try:
            hint = world_reqs.environment_hint or FALLBACK_ENVIRONMENT_ID
            kit = load_environment(str(hint))
        except Exception:  # noqa: BLE001 - environment failure degrades safe
            deferred.append("world_requirements stage: unsupported environment")
            return None
        try:
            composition = compose_world(
                world_reqs,
                env_resolver=None,
                oracle=None,
                spec_provider=spec_adapter,
                cache=self._generated_cache,
                evidence_placements=(),
                catalog=self._catalog,
                kit=kit,
            )
        except Exception:  # noqa: BLE001 - compose degrades safe
            deferred.append("world composition failed for the requested world")
            return None
        if not isinstance(composition, WorldComposition):
            deferred.append("world composition produced no usable result")
            return None
        if composition.issues:
            deferred.extend(composition.issues)
        return composition

    def _assemble(
        self,
        attempt: Any,
        crime: Any,
        public: Any,
        evidence_spec: Any,
        world_reqs: WorldRequirements,
        composition: Any,
    ) -> Any:
        """Build the ``GeneratedDraft`` from the staged outputs + composition."""
        from app.environments.compose import scene_for_kit
        from app.environments.manifests import load_environment
        from app.environments.resolver import FALLBACK_ENVIRONMENT_ID
        from app.generation.publish import build_published_case_version  # noqa: F401
        from app.generation.schemas import (
            GeneratedDraft,
            LocationSpec,
            ObjectSpec,
            SceneSpec,
            WorldGraphLocationSpec,
            WorldGraphSpec,
        )
        from app.world.composer import KIT_BASE_OBJECT_IDS

        if crime is None:
            # no case truth -> validate_draft reports "incomplete" (controller
            # classifies TERMINAL/REPAIR through the normal path).
            return None

        # Resolve the final kit for the scene + world graph.
        hint = (world_reqs.environment_hint if world_reqs else None) or FALLBACK_ENVIRONMENT_ID
        try:
            kit = load_environment(str(hint))
        except Exception:  # noqa: BLE001
            kit = None

        # base public objects (kit base set) with golden affordances.
        objects: list[ObjectSpec] = []
        if kit is not None:
            for object_id in KIT_BASE_OBJECT_IDS.get(
                kit.environment_id, KIT_BASE_OBJECT_IDS["apartment"]
            ):
                spec = _base_object_spec(object_id)
                if spec is not None and not any(o.object_id == spec.object_id for o in objects):
                    objects.append(spec)

        # composition placements -> world graph + new public objects.
        placements: list[Any] = []
        new_objects: list[ObjectSpec] = []
        if composition is not None:
            placements = list(composition.placements)
            for obj in composition.new_objects:
                if not any(o.object_id == obj.object_id for o in objects):
                    new_objects.append(_enhance_weapon(attempt, obj))
        objects = [*objects, *new_objects]

        scene: SceneSpec | None = None
        world_graph = WorldGraphSpec()
        if kit is not None:
            scene = scene_for_kit(kit, public.scene if public is not None else None)
            if composition is not None:
                world_graph = WorldGraphSpec(
                    locations=tuple(
                        WorldGraphLocationSpec(
                            location_id=zone.zone_id,
                            template=f"{kit.environment_id}_template",
                            rooms=zone.rooms,
                        )
                        for zone in kit.zones
                    ),
                    placements=tuple(placements),
                )

        return GeneratedDraft(
            crime=crime,
            persons=tuple(public.persons) if public is not None and public.persons else (),
            motives=tuple(public.motives) if public is not None and public.motives else (),
            objects=tuple(objects),
            locations=(
                tuple(public.locations)
                if public is not None and public.locations
                else (LocationSpec(location_id=crime.location_id, name=crime.location_id),)
            ),
            travel_rules=tuple(public.travel_rules) if public is not None else (),
            scene=scene,
            evidence=tuple(evidence_spec.evidence) if evidence_spec is not None else (),
            world_graph=world_graph,
        )


# --------------------------------------------------------------------------- #
# base kit object specs (app-owned mirror of the golden base set so the weapon/
# evidence candidate universes stay correct for a driver-produced draft).
# --------------------------------------------------------------------------- #


def _enhance_weapon(attempt: Any, obj: ObjectSpec) -> ObjectSpec:
    """Deterministically grant weapon affordances to a crime-critical unknown
    object that IS the locked weapon.

    The LLM proposes the object; the DETERMINISTIC driver decides universe
    membership. When a procedural object's id normalizes to the LOCKED weapon
    it enters the weapon universe (POTENTIAL_WEAPON + POTENTIAL_SHARP_WEAPON)
    so the solver can derive it — this is the showcase path that lets a
    crime-critical unseen object be the actual weapon the deduction proves.
    """
    from app.generation.constraints import normalize_identity
    from app.generation.schemas import ObjectSpec

    locked = getattr(attempt, "locked", None)
    weapon = getattr(locked, "weapon", None) if locked is not None else None
    if not isinstance(weapon, str) or not weapon:
        return obj
    if normalize_identity(weapon) != normalize_identity(obj.object_id):
        return obj
    return ObjectSpec(
        object_id=obj.object_id,
        asset_id=obj.asset_id,
        affordances=("INSPECTABLE", "POTENTIAL_WEAPON", "POTENTIAL_SHARP_WEAPON"),
        subtype=obj.subtype,
    )


def _base_object_spec(object_id: str) -> ObjectSpec | None:
    from app.generation.schemas import ObjectSpec

    _BASE: dict[str, tuple[str, tuple[str, ...], str | None]] = {
        "kitchen_knife": (
            "PROP_KITCHEN_KNIFE_01",
            ("INSPECTABLE", "POTENTIAL_WEAPON", "POTENTIAL_SHARP_WEAPON"),
            "sharp_weapon",
        ),
        "letter_opener": (
            "PROP_LETTER_OPENER_01",
            ("INSPECTABLE", "POTENTIAL_WEAPON", "POTENTIAL_SHARP_WEAPON"),
            "sharp_weapon",
        ),
        "scissors": (
            "PROP_SCISSORS_01",
            ("INSPECTABLE", "POTENTIAL_WEAPON", "POTENTIAL_SHARP_WEAPON"),
            "sharp_weapon",
        ),
        "vase_01": ("PROP_VASE_01", ("INSPECTABLE",), None),
        "apartment_table": ("PROP_TABLE_01", ("INSPECTABLE",), "furniture"),
        "apartment_door": ("DOOR_APARTMENT_01", ("INSPECTABLE",), "door"),
        "apartment_lamp": ("PROP_LAMP_01", ("INSPECTABLE",), "light"),
        "apartment_laptop": ("PROP_LAPTOP_01", ("INSPECTABLE",), "electronics"),
        "victim_body_placeholder": ("PROP_BODY_PLACEHOLDER_01", ("INSPECTABLE",), "victim_body"),
    }
    entry = _BASE.get(object_id)
    if entry is None:
        return None
    asset_id, affordances, subtype = entry
    return ObjectSpec(
        object_id=object_id,
        asset_id=asset_id,
        affordances=affordances,
        subtype=subtype,
    )


__all__ = [
    "MAX_SPEC_REPAIR_PASSES",
    "OllamaAssetSpecProvider",
    "OllamaStageDriver",
    "StageDriverProviderFailure",
    "parse_case_people",
    "parse_world_requirements",
]
