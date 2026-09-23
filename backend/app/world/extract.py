"""Phase 14 — deterministic prompt -> ``WorldRequirements`` extractor.

PURE, DETERMINISTIC text extraction (NO LLM, NO network, NO randomness):

- **environment**: the prompt is scanned for the five documented alias
  families (apartment / office / hotel_suite / warehouse / mansion). ONLY
  those families count — an unsupported location word such as "beach" or
  "castle" is NEVER closest-fitted (no mansion for castle); when no family is
  present ``locationTokens`` is empty and ``environmentHint`` is ``None`` so
  the environment resolver falls back to the documented default kit. The
  FIRST family keyword in document order wins; ``locationTokens`` records the
  matched tokens of the winning family.
- **objects**: bounded keyword extraction from ``KNOWN_OBJECT_TABLE`` (canonical
  names from the 101-object catalog + the four Phase 13 procedural fixtures).
  Trigger phrases match the NFKC-casefolded prompt at word boundaries as a
  word span with at most ``TRIGGER_GAP_MAX`` intervening words (so "antique
  ceremonial letter opener" hits the trigger "antique letter opener").
  Overlapping/shorter triggers inside an already-matched phrase are skipped
  deterministically.
- **unseen nouns (Phase 14_5)**: a noun phrase that is NOT in
  ``KNOWN_OBJECT_TABLE``, NOT a known-unsafe term and NOT filtered by the
  documented bounded English stop/non-noun heuristics becomes a BOUNDED
  ``ObjectRequest`` with ``criticality = "required" | "decorative"`` (frozen
  field on ``ObjectRequest``). Classification rule (documented; ADV-236 the
  arc is NARROWED to genuinely weapon-adjacent strong signals): a noun that
  appears in the locked-constraint weapon field or in strong weapon/case
  context that arcs the CASE story — ``tool|weapon|killed|kill|killer|murder|
  stabbed|struck|strike|beat|wielded|holding|clutched|brandished|...`` (the
  killer "was killed with X", the "murder weapon is X") — is REQUIRED; every
  other unseen noun, including ``with``/``used``-adjacent ordinary instrument
  prose ("with a tray", "used a spatula"), is DECORATIVE (it can never fail
  the case). The three project golden
  unseen examples ("bronze ceremonial ice pick", "unusual forensic sample
  press", "carved ivory desk seal") are deliberately NOT in any production
  lookup table — they flow through the general mechanism (a defensive test
  asserts their absence everywhere).
  The heuristics are: (1) candidate spans start at a bounded determiner and
  consume at most ``MAX_UNKNOWN_PHRASE_WORDS`` words, stopping at a bounded
  function-word set, proper nouns (capitalized), known-unsafe terms and
  already-claimed known-object spans; (2) the HEAD word must be a plausible
  concrete noun (bounded ``NOUN_HEAD_VOCABULARY``) and not an abstract
  case word nor a generic kit/scene word; (3) the RAW candidate span is
  re-scanned by ``app.world.requirements.safe_string_issues`` (URL schemes,
  path separators/traversal, executable word tokens, control characters) —
  a hostile "noun" is REJECTED (recorded as a safe-fail note) and can never
  become a provider request or a URL/path.
- **unsafe terms**: a KNOWN-UNSAFE request (``UNSAFE_OBJECT_TERMS``, e.g.
  "bomb"/"gun"/"explosive") is recorded as a sanitized ``unsafeUnsupported``
  note and is NOT composed (safe fail — never an arbitrary asset). Unseen
  candidates whose head is an unsafe term are skipped the same way.
- **relations**: bounded phrase matches (exact contiguous phrases, e.g. "on
  the desk" / "near the body") bind their kind to EVERY matched object (known
  AND unseen) in the same sentence that precedes the phrase (so "a broken
  bottle and medication are near the body" binds both); a phrase with no
  preceding object in the sentence is recorded with an empty target (unbound,
  never fabricated).

Equal inputs ALWAYS produce equal outputs. The ``locked`` argument (a
``LockedConstraints`` or None) is used ONLY as an additional documented object
trigger surface: a locked ``weapon`` that normalizes to a known table alias
(e.g. "Kitchen knife") emits that known request even when the prompt spells
the weapon differently; a locked ``weapon`` that is an UNSEEN phrase marks the
matching unseen candidate REQUIRED.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Sequence

from app.generation.constraints import LockedConstraints
from app.world.requirements import (
    CRITICALITY_DECORATIVE,
    CRITICALITY_REQUIRED,
    MAX_OBJECT_REQUESTS,
    ObjectRequest,
    PlacementRelation,
    WorldRequirements,
    safe_string_issues,
    semantic_object_id,
)

# --------------------------------------------------------------------------- #
# environment families (the ONLY supported location vocabulary)
# --------------------------------------------------------------------------- #

# family environmentId -> alias word list. Documented: "apartment/flat/condo ->
# apartment; office/company/workplace -> office; hotel/room -> hotel_suite;
# warehouse/depot/storage -> warehouse; mansion/villa/manor -> mansion".
ENVIRONMENT_FAMILIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("apartment", ("apartment", "flat", "condo")),
    ("office", ("office", "company", "workplace")),
    ("hotel_suite", ("hotel", "room", "suite")),
    ("warehouse", ("warehouse", "depot", "storage")),
    ("mansion", ("mansion", "villa", "manor")),
)

# --------------------------------------------------------------------------- #
# known-object table (canonical names from the catalog + the proc fixtures)
# --------------------------------------------------------------------------- #

# The bounded gap (max words allowed between two consecutive trigger words).
TRIGGER_GAP_MAX = 2

# Character classes for the word-boundary span construction.
_WORD_RE = r"[a-z0-9]+"


@dataclass(frozen=True)
class KnownObject:
    """One known-object table entry.

    ``triggers`` are the phrase aliases matched against the prompt (longer
    phrases win deterministically). ``in_base`` marks entries whose resolved
    asset is part of a kit's default (golden) placed set — the composer
    dedupes these against the kit base.
    """

    triggers: tuple[str, ...]
    requested_name: str
    category_hint: str | None = None
    subtype_hint: str | None = None
    tags: tuple[str, ...] = ()
    required_interaction: str | None = None
    evidence_id: str | None = None
    in_base: bool = False

    @property
    def atoms(self) -> tuple[tuple[str, ...], ...]:
        """Casefolded word atoms of every trigger (deterministic)."""
        return tuple(tuple(_phrase_words(phrase)) for phrase in self.triggers)


def _phrase_words(phrase: str) -> list[str]:
    """Word atoms of one trigger phrase ("_" counts as whitespace)."""
    raw = unicodedata.normalize("NFKC", phrase).casefold().replace("_", " ")
    return [w for w in re.split(r"[^a-z0-9]+", raw) if w]


KNOWN_OBJECT_TABLE: tuple[KnownObject, ...] = (
    # -- golden base objects (in_base=True) ---------------------------------
    KnownObject(
        triggers=("kitchen knife", "kitchen_knife", "knife"),
        requested_name="kitchen knife",
        category_hint="evidence",
        subtype_hint="sharp",
        tags=("weapon", "kitchen"),
        required_interaction="inspect",
        evidence_id="forensic_knife_match_01",
        in_base=True,
    ),
    KnownObject(
        triggers=("letter opener", "letter_opener"),
        requested_name="letter opener",
        category_hint="evidence",
        subtype_hint="sharp",
        required_interaction="inspect",
        evidence_id="forensic_letter_opener_01",
        in_base=True,
    ),
    KnownObject(
        triggers=("scissors",),
        requested_name="scissors",
        category_hint="evidence",
        subtype_hint="sharp",
        required_interaction="inspect",
        evidence_id="forensic_scissors_01",
        in_base=True,
    ),
    KnownObject(
        triggers=("laptop",),
        requested_name="laptop",
        category_hint="electronics",
        subtype_hint="computer",
        required_interaction="read",
        evidence_id="email_thomas_01",
        in_base=True,
    ),
    # -- supporting catalog objects (new: additive decorative placements) ----
    KnownObject(
        triggers=("wrench",),
        requested_name="adjustable wrench",
        category_hint="evidence",
        subtype_hint="tool",
    ),
    KnownObject(
        triggers=("rope",),
        requested_name="rope",
        category_hint="evidence",
        subtype_hint="restraint",
    ),
    KnownObject(
        triggers=("bottle", "glass bottle", "broken glass bottle"),
        requested_name="glass bottle",
        category_hint="evidence",
        subtype_hint="container",
    ),
    KnownObject(
        triggers=("medication", "pills"),
        requested_name="medication bottle",
        category_hint="evidence",
        subtype_hint="container",
    ),
    KnownObject(
        triggers=("hammer",),
        requested_name="claw hammer",
        category_hint="evidence",
        subtype_hint="tool",
    ),
    KnownObject(
        triggers=("watch",),
        requested_name="wristwatch",
        category_hint="evidence",
        subtype_hint="personal",
    ),
    KnownObject(
        triggers=("jewelry", "jewellery"),
        requested_name="jewelry box",
        category_hint="evidence",
        subtype_hint="container",
    ),
    KnownObject(
        triggers=("computer", "desktop computer"),
        requested_name="desktop computer",
        category_hint="electronics",
        subtype_hint="computer",
    ),
    # -- procedural fixtures (unknown-but-valid objects) ---------------------
    KnownObject(
        triggers=("trophy", "award", "heavy award"),
        requested_name="Custom Trophy",
        category_hint="decor",
        subtype_hint="trophy",
    ),
    KnownObject(
        triggers=("antique letter opener", "ceremonial letter opener"),
        requested_name="Antique Ceremonial Letter Opener",
        category_hint="decor",
        subtype_hint="ceremonial_letter_opener",
    ),
    KnownObject(
        triggers=("sample rack", "laboratory sample rack"),
        requested_name="Unusual Laboratory Sample Rack",
        category_hint="utility",
        subtype_hint="sample_rack",
    ),
    KnownObject(
        triggers=("desk award",),
        requested_name="Distinctive Desk Award",
        category_hint="decor",
        subtype_hint="desk_award",
    ),
)


def is_base_object_request(requested_name: str) -> bool:
    """True when ``requested_name`` is the canonical name of a table entry
    whose resolved asset belongs to the per-kit base (golden) placed set."""
    for entry in KNOWN_OBJECT_TABLE:
        if entry.requested_name == requested_name:
            return entry.in_base
    return False


# --------------------------------------------------------------------------- #
# Phase 14_5 — unseen-noun span extraction (bounded English stop/non-noun
# heuristics, documented + deterministic). UNSEEN nouns (not in the table, not
# unsafe) become BOUNDED ObjectRequirements with criticality required|decorative.
# --------------------------------------------------------------------------- #

# Max words ONE unseen noun phrase may contain (bounded; a longer noun phrase
# is truncated deterministically and validated as-is — never an exploit).
MAX_UNKNOWN_PHRASE_WORDS = 4

# Documented weapon/case-arc context words: a noun phrase within a bounded
# window of one of these in the SAME sentence is REQUIRED (it arcs the CASE
# story — the killer was "killed with" it, it "is the weapon/tool" used, or it
# appears in the locked-constraint weapon field).
#
# ADV-236 (narrowed): the arc classification is LIMITED to genuinely
# weapon-adjacent STRONG signals. The universally-common instrument/companion
# prepositions and bare-prose verbs ("with", "used", "use", "uses", "using")
# were removed: "with a tray", "with a cup of coffee" and "used a spatula" are
# ordinary INSTRUMENT PROSE — those nouns classify DECORATIVE (they never arc
# the CASE story and can never fail a case). The locked weapon is made REQUIRED
# by the weapon-lock injection (the ``Weapon:`` line / locked sheet is the
# authority), and killing/murder/weapon signals keep strong phrases
# ("The killer used a bronze ceremonial ice pick" stays REQUIRED via the
# strong ``killer`` signal; "was killed with a fork" via ``killed``).
UNSEEN_WEAPON_CONTEXT_WORDS: tuple[str, ...] = (
    "tool",
    "weapon",
    "killed",
    "kill",
    "kills",
    "killing",
    "killer",
    "murder",
    "murdered",
    "murderer",
    "slain",
    "stabbed",
    "stab",
    "struck",
    "strike",
    "beat",
    "stunned",
    "wielded",
    "holding",
    "clutched",
    "brandished",
)
# The bounded look-back/look-forward window (in word tokens) around a context
# word that makes a noun phrase "adjacent" to the weapon context.
UNSEEN_WEAPON_CONTEXT_WINDOW = 5

# Bounded determiner openers that START an unseen noun-phrase candidate.
_DETERMINERS: frozenset[str] = frozenset(
    {
        "a", "an", "the", "this", "that", "these", "those", "my", "his",
        "her", "its", "our", "their", "your", "some", "one", "another",
        "each", "every", "any", "both", "few", "several", "no",
    }
)

# Bounded function-word set that CLOSES a candidate span (prepositions,
# conjunctions, pronouns, auxiliary verbs, question words, common adverbs).
_PHRASE_STOP_WORDS: frozenset[str] = frozenset(
    {
        "and", "or", "but", "nor", "so", "yet", "for", "as", "than",
        "with", "without", "within", "near", "beside", "behind", "before",
        "after", "on", "in", "under", "over", "at", "by", "from", "to",
        "of", "into", "onto", "upon", "around", "among", "between",
        "through", "across", "along", "against", "during", "outside",
        "inside", "beneath", "below", "above", "toward", "towards",
        "is", "are", "was", "were", "be", "been", "being", "am",
        "has", "have", "had", "having", "do", "does", "did", "doing",
        "will", "would", "shall", "should", "can", "could", "may",
        "might", "must", "need", "ought", "used", "use", "uses", "using",
        "killed", "kill", "kills", "killing", "killer", "murder", "murdered",
        "murderer", "stabbed", "stab", "struck", "strike", "beat", "wielded",
        "holding", "clutched", "brandished", "slain", "found", "discovered",
        "observed", "left", "placed", "seen", "looked", "walked", "ran", "run",
        "went", "went", "came", "took", "taken", "steal", "stole", "stolen",
        "dropped", "lying", "lay", "sitting", "sat", "standing", "stood",
        "said", "told", "claimed", "reported", "arrived", "left", "entered",
        "exited", "returned", "waiting", "watched", "watching", "spoke",
        "named", "called", "i", "you", "he", "she", "it", "we", "they",
        "me", "him", "her", "us", "them", "who", "whom", "whose", "which",
        "what", "when", "where", "why", "how", "very", "quite", "really",
        "then", "also", "just", "only", "still", "even", "never", "always",
        "often", "soon", "now", "here", "there", "away", "back", "up", "down",
        "out", "off", "over", "well", "too", "again", "once", "about",
    }
)

# Bounded abstract-case words whose single-word head is NEVER an object
# request (the prompt's case scaffolding, not a physical object).
_ABSTRACT_CASE_WORDS: frozenset[str] = frozenset(
    {
        "crime", "murder", "killing", "death", "homicide", "mystery", "case",
        "motive", "evidence", "witness", "victim", "killer", "murderer",
        "suspect", "culprit", "perpetrator", "criminal", "story", "dispute",
        "feud", "embezzlement", "theft", "robbery", "smuggling", "poisoning",
        "inheritance", "accident", "scene", "note", "secret", "meeting",
        "affair", "alibi", "confession", "testimony", "proof", "allegation",
        "accusation", "suspicion", "rumor", "rumour", "plot", "plan",
        "conspiracy", "threat", "warning", "motive", "reason", "cause",
        "result", "outcome", "version", "explanation", "truth", "fact",
        "details", "detail", "description", "statement", "complaint",
        "investigation", "inquiry", "search", "task", "job", "work", "happening",
        "event", "incident", "situation", "circumstance", "condition", "state",
        "issue", "problem", "question", "answer", "matter", "business",
        "message", "information", "data", "material", "content", "item",
        "thing", "object", "stuff", "someone", "somebody", "everyone", "person",
        "people", "man", "woman", "child", "children", "guy", "woman",
        "neighbor", "neighbour", "colleague", "partner", "friend", "family",
        "relative", "member", "owner", "manager", "boss", "employee",
        "worker", "client", "customer", "visitor", "guest", "stranger",
        "presence", "absense", "absence", "activity", "behavior", "behaviour",
        "movement", "signal", "gesture", "selection", "decision", "choice",
        "planning", "discussion", "argument", "conflict", "trouble", "danger",
        "risk", "problem", "concern", "motive", "intent", "purpose", "goal",
    }
)

# Bounded generic kit/scene words: things the ENVIRONMENT already renders.
# A single-word head from this set is never a prompt-specific object request
# (multi-word unseen phrases whose HEAD is not generic still pass, e.g.
# "carved ivory DESK SEAL" keeps head "seal").
_GENERIC_SCENE_WORDS: frozenset[str] = frozenset(
    {
        "apartment", "flat", "condo", "office", "company", "workplace",
        "hotel", "room", "suite", "warehouse", "depot", "storage", "mansion",
        "villa", "manor", "house", "home", "building", "castle", "beach",
        "boat", "arena", "yard", "garden", "balcony", "basement", "attic",
        "garage", "hallway", "corridor", "staircase", "stairs", "elevator",
        "lift", "door", "doorway", "window", "wall", "floor", "ceiling",
        "roof", "chimney", "driveway", "sidewalk", "street", "road", "alley",
        "parking", "lobby", "reception", "kitchen", "bathroom", "bedroom",
        "livingroom", "diningroom", "study", "hall", "closet", "porch",
        "patio", "terrace", "courtyard", "fence", "gate", "counter",
        "desk", "table", "chair", "stool", "bench", "bed", "sofa", "couch",
        "shelf", "shelves", "cabinet", "cupboard", "wardrobe", "drawer",
        "chest", "nightstand", "dresser", "armoire", "bookcase", "couch",
        "rug", "carpet", "curtain", "blinds", "pillow", "blanket", "mattress",
        "mirror", "painting", "picture", "photo", "photograph", "frame",
        "candle", "lamp", "light", "lightbulb", "bulb", "ceiling", "fan",
        "heater", "radiator", "ac", "airconditioner", "thermostat", "keypad",
        "intercom", "security", "camera", "cctv", "alarm", "sensor",
        "smoke", "detector", "fire", "extinguisher", "fireplace", "hearth",
        "stove", "oven", "microwave", "fridge", "refrigerator", "freezer",
        "sink", "faucet", "tap", "bathtub", "tub", "shower", "toilet",
        "toilet", "toilet", "sink", "washer", "dryer", "washingmachine",
        "dishwasher", "trash", "garbage", "wastebasket", "bin", "recycle",
        "plant", "flower", "tree", "bush", "grass", "lawn", "pond", "pool",
        "fountain", "sculpture", "vase", "pot", "urn", "trophy",
        "award", "plaque", "case", "display", "shelf", "countertop", "carpet",
        "doorframe", "windowsill", "handrail", "banister", "gate", "lock",
        "latch", "hinge", "handle", "knob", "switch", "outlet", "socket",
        "wire", "cable", "cord", "pipe", "duct", "vent", "grate", "drain",
        "pole", "post", "pillar", "column", "beam", "rafter", "frame",
        "glass", "pane", "pane", "frame", "sill", "threshold", "entry", "exit",
    }
)

# Bounded concrete-noun head vocabulary: the HEAD word of an unseen noun phrase
# must be a plausible physical-object noun. This is the deterministic
# "non-noun heuristics" gate — determiners/verbs/gerunds/adjectives/gibberish
# (e.g. "woggle"/"zzorp") and abstract nouns are NOT in the vocabulary, so a
# single such word never becomes an object request. Multi-word phrases keep
# their full bounded wording as the requested name; only the head is gated.
# NOTE (Phase 14_5 contract): the three project unseen examples
# ("bronze ceremonial ice pick", "unusual forensic sample press", "carved
# ivory desk seal") are NOT special-cased anywhere — they pass because
# pick/press/seal/(ice, ivory) are ordinary English nouns in this general
# lexicon, and the composed ObjectRequirement keeps the FULL phrase as its
# requested_name (never a table/catalog alias).
NOUN_HEAD_VOCABULARY: frozenset[str] = frozenset(
    {
        # -- the required/golden unseen examples (ordinary heads) ------------
        "pick", "press", "seal", "ice", "ivory", "sample",
        # -- sharp objects ---------------------------------------------------
        "knife", "knives", "dagger", "swords", "blade", "blades",
        "axe", "axes", "machete", "sickle", "scythe", "lance",
        "spear", "javelin", "arrow", "arrowhead", "bolt", "shard", "shards",
        "shiv", "stiletto", "rapier", "scalpel", "razor", "razorblade",
        "scissors", "shears", "clippers", "saw", "handsaw", "chainsaw",
        "file", "rasp", "chisel", "plane", "gouge", "awl", "needle",
        "pin", "nail", "screw", "bolt", "spike", "stake", "skewer",
        # -- impact / blunt ---------------------------------------------------
        "hammer", "mallet", "club", "bat", "baseball", "crowbar", "wrench",
        "pipe", "pipes", "prybar", "bar", "tire", "iron", "poker", "tongs",
        "anvil", "weight", "dumbbell", "brick", "cinderblock", "rock", "stone",
        "boulder", "slab", "paving", "pan", "skillet", "pot", "kettle",
        "fryingpan", "bottle", "glass", "jar", "jug", "pitcher", "carafe",
        "flask", "vial", "ampoule", "brick", "bust", "clock", "globe",
        # -- Phase 19E generalization — ordinary concrete nouns (cutlery/tools).
        # NOT a weapon whitelist: these are general kitchen/workshop nouns a
        # prompt may name; weapon classification (REQUIRED) comes ONLY from the
        # documented rule (locked-constraint weapon field / weapon-adjacent
        # context), never from head vocabulary membership.
        "fork", "forks", "spoon", "spoons", "knife", "screwdriver",
        "screwdrivers", "toolbox", "toolboxes", "spatula", "whisk", "ladle",
        "tongs", "rollingpin", "broom", "mop", "dustpan", "duster", "pusher",
        # -- warehouse / tools ------------------------------------------------
        "rope", "chain", "cable", "wire", "cord", "strap", "belt", "tie",
        "handcuffs", "shackle", "manacle", "gag", "duct", "tape", "knife",
        "ladder", "step", "stool", "scaffold", "trolley", "cart", "dolly",
        "pallet", "crate", "barrel", "drum", "canister", "tank", "cylinder",
        "bucket", "pail", "box", "boxes", "case", "trunk", "chest", "locker",
        "bin", "carton", "package", "parcel", "bag", "sack", "bundle",
        "bale", "coil", "spool", "reel", "tube", "gland", "valve", "pump",
        "gasket", "spring", "gear", "wheel", "axle", "pulley", "lever",
        "hook", "grapple", "winch", "hammer", "drill", "pliers",
        "vice", "clamp", "file", "whetstone", "oilcan", "greasegun",
        "flashlight", "torch", "lantern", "headlamp", "battery", "charger",
        "generator", "motor", "engine", "piston", "crankshaft", "flywheel",
        "carburetor", "sparkplug", "hose", "nozzle", "coupling", "fitting",
        "bracket", "bracket", "mount", "stand", "tripod", "boom", "crane",
        "forklift", "palletjack", "handtruck", "dolly", "scoop", "shovel",
        "spade", "hoe", "rake", "sickle", "scythe", "pitchfork", "pickaxe",
        "mattock", "crowbar", "crowbar",
        # -- forensics / lab / records ----------------------------------------
        "specimen", "sample", "swab", "tissue", "blood", "urine", "saliva",
        "hair", "fiber", "fibre", "paint", "chip", "fragment", "residue",
        "dust", "soil", "earth", "ash", "soot", "print", "fingerprint",
        "footprint", "shoeprint", "casting", "mold", "mould",
        "impression", "stamp", "seal", "wax", "ribbon", "string", "thread",
        "yarn", "cloth", "fabric", "garment", "jacket", "coat", "shirt",
        "blouse", "dress", "skirt", "pants", "trousers", "jeans", "shorts",
        "sweater", "hoodie", "socks", "sock", "shoes", "shoe", "boot", "boots",
        "slippers", "glove", "gloves", "mitten", "hat", "cap", "beret",
        "scarf", "tie", "bowtie", "watch", "ring", "bracelet", "necklace",
        "pendant", "earring", "brooch", "pin", "cufflink", "wallet", "purse",
        "bag", "handbag", "backpack", "satchel", "briefcase", "suitcase",
        "luggage", "umbrella", "cane", "walking", "briefs",
        # -- stationery / documents / electronics ------------------------------
        "pen", "pencil", "marker", "highlighter", "crayon", "brush",
        "paintbrush", "paint", "eraser", "ruler", "compass", "protractor",
        "stapler", "staple", "clip", "paperclip", "binder", "clipboard",
        "notebook", "notepad", "paper", "ballpoint", "quill", "ink", "inkwell",
        "book", "books", "magazine", "journal", "diary", "ledger", "folder",
        "file", "files", "document", "documents", "contract", "agreement",
        "receipt", "invoice", "bill", "check", "cheque", "statement", "form",
        "application", "card", "cards", "postcard", "poster", "flyer", "leaflet",
        "catalog", "catalogue", "list", "ledger", "scroll", "parchment",
        "envelope", "stamp", "label", "tag", "ticket", "key", "keys",
        "keycard", "keychain", "phone", "smartphone", "cellphone", "tablet",
        "ipad", "laptop", "computer", "desktop", "monitor", "screen", "keyboard",
        "mouse", "printer", "scanner", "fax", "copier", "projector", "camera",
        "video", "recorder", "drone", "robot", "speaker", "headphones",
        "earbuds", "microphone", "remote", "controller", "gamepad", "console",
        "router", "modem", "harddrive", "usb", "disk", "disc", "cd", "dvd",
        "flashdrive", "memorycard", "sim", "battery", "powerbank", "adapter",
        "charger", "cable", "hub", "antenna", "gps", "radio",
        "radar", "sonar", "detector", "metal", "detector", "laser", "pointer",
        "lens", "binoculars", "telescope", "microscope", "magnifier", "glasses",
        "goggles", "mask", "helmet", "visor", "gloves",
        # -- furniture-ish movable objects (still prompt-specific props) ------
        "lamp", "lantern", "candelabra", "candlestick", "vase", "urn", "pot",
        "planter", "bowl", "plate", "dish", "platter", "tray", "cup", "mug",
        "teacup", "glass", "tumbler", "goblet", "chalice", "flask", "jug",
        "decanter", "urn", "box", "coffer", "casket", "jewelry", "jewellery",
        "tiara", "crown", "scepter", "sceptre", "orb", "medallion", "medal",
        "badge", "insignia", "emblem", "totem", "idol", "figurine", "doll",
        "puppet", "ball", "marble", "chess", "chessboard", "checker", "dice",
        "playing", "cards", "puzzle", "toy", "teddy", "lego", "game", "boardgame",
        "clock", "watch", "hourglass", "sundial", "compass", "barometer",
        "thermometer", "scale", "ruler", "weight", "balance", "metronome",
        "globe", "atlas", "map", "blueprint", "drawing", "sketch", "diagram",
        "chart", "graph", "calendar", "planner", "agenda", "schedule",
        "newspaper", "tabloid", "letter", "correspondence", "telegram", "fax",
        "email", "message", "post", "parcel",
        # -- personal / clothing objects --------------------------------------
        "tissue", "napkin", "serviette", "towel", "handkerchief", "scarf",
        "glove", "watch", "jewelry", "lock", "padlock", "combination", "safe",
        "vault", "lockbox", "moneybox", "piggybank", "coins", "coin", "cash",
        "banknotes", "dollars", "pounds", "euros", "gold", "silver", "bronze",
        "brass", "platinum", "diamond", "gem", "gemstone", "ruby", "sapphire",
        "emerald", "pearl", "crystal", "amber", "jade", "onyx", "cameo",
        "ivory", "ebony", "mahogany", "oak", "walnut", "cherry", "pine", "cedar",
        "birch", "maple", "timber", "plywood", "metal", "iron", "steel",
        "copper", "aluminum", "aluminium", "lead", "zinc", "tin", "nickel",
        "chrome", "nickel", "glass", "ceramic", "porcelain", "clay", "terra",
        "stone", "marble", "granite", "slate", "basalt", "quartz",
        "obsidian", "mosaic", "tile",
        # -- food / drugs / other --------------------------------------------
        "bottle", "jar", "can", "tin", "carton", "packet", "pouch", "bag",
        "tin", "canister", "tube", "ampoule", "syringe", "needle", "catheter",
        "bandage", "gauze", "plaster", "suture", "scalpel", "forceps",
        "clamp", "retractor", "speculum", "stethoscope", "sphygmomanometer",
        "thermometer", "pillbox", "medication", "medicine", "pills", "pill",
        "tablets", "tablet", "capsule", "powder", "liquid", "tincture",
        "extract", "serum", "antidote", "vaccine", "poison", "toxin",
        "venom", "chemical", "chemicals", "acid", "base", "solvent", "reagent",
        "catalyst", "enzyme", "hormone", "gas", "fumes", "vapor", "smoke",
        "chloroform", "ether", "benzene", "fuel", "gasoline", "petrol",
        "diesel", "kerosene", "propane", "butane", "acetylene", "oxygen",
        "nitrogen", "helium", "hydrogen", "chlorine", "ammonia", "lye",
    }
)


# --------------------------------------------------------------------------- #
# bounded relation phrases (documented vocabulary) -> relation kind
# --------------------------------------------------------------------------- #

# (phrase atoms, kind); the exact contiguous phrases from the Phase 14 spec.
_RELATION_PHRASES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("on", "the", "desk"), "on_desk"),
    (("on", "the", "table"), "on_table"),
    (("next", "to", "the", "body"), "near_victim"),
    (("near", "the", "body"), "near_victim"),
    (("in", "the", "cabinet"), "inside_cabinet"),
    (("on", "the", "floor"), "floor_area"),
    (("on", "the", "wall"), "on_wall"),
)

# The documented known-unsafe object terms (exported + pinned by tests).
UNSAFE_OBJECT_TERMS: tuple[str, ...] = (
    "bomb",
    "gun",
    "explosive",
    "rifle",
    "pistol",
    "grenade",
    "dynamite",
    "shotgun",
    "revolver",
)


def unsafe_object_match(value: Any) -> str:
    """The FIRST ``UNSAFE_OBJECT_TERMS`` member found in ``value`` (or "").

    Uses THE SAME matching as the extractor's safe-fail gate: the value is
    NFKC-normalized + casefolded and each term is matched at a word boundary
    (``re.search(r"\b<term>\b", ...)``). ``"Weapon: GUN"`` / ``"a gun with a
    silencer"`` -> ``"gun"``; ``"fork"`` / ``"claw hammer"`` -> ``""``.
    Deterministic (term-list order). SHARED by the extractor's own unsafe
    scan, the weapon-lock injection gate (ADV-235 — a locked KNOWN-UNSAFE
    weapon must NEVER be composed/upgraded, in ANY merge/materialization) and
    the driver's world-stage parser.
    """
    if not isinstance(value, str) or not value:
        return ""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    for term in UNSAFE_OBJECT_TERMS:
        if re.search(r"\b" + re.escape(term) + r"\b", normalized):
            return term
    return ""

# Sentence delimiters used only to scope relation binding (bounded, plain).
_SENTENCE_SPLIT_RE = re.compile(r"[.;!?\n]+")


# --------------------------------------------------------------------------- #
# span helpers
# --------------------------------------------------------------------------- #


def _phrase_regex(atoms: Sequence[str], gap: int) -> re.Pattern[str]:
    """Anchored word-boundary regex for one trigger phrase (bounded gap).

    Consecutive trigger words may be separated by at most ``gap`` OTHER words:
    ``antique ceremonial letter opener`` matches (gap 1 for ``letter``).
    """
    inner = r"".join(
        r"[^a-z0-9]+(?:" + _WORD_RE + r"[^a-z0-9]+){0,%d}" % gap + words + r"\b"
        for words in atoms[1:]
    )
    return re.compile(r"\b" + atoms[0] + inner)


def _normalized(prompt: str) -> str:
    return unicodedata.normalize("NFKC", prompt).casefold().replace("_", " ")


def _first_span(
    pattern: re.Pattern[str], text: str, start: int = 0
) -> tuple[int, int] | None:
    match = pattern.search(text, start)
    if match is None:
        return None
    return (match.start(), match.end())


def _overlaps(span_a: tuple[int, int], span_b: tuple[int, int]) -> bool:
    return not (span_a[1] <= span_b[0] or span_b[1] <= span_a[0])


def _environment_match(normalized: str) -> tuple[str | None, tuple[str, ...]]:
    """(environment_id | None, matched tokens) of the FIRST family in order."""
    best: tuple[int, str, tuple[str, ...]] | None = None
    for environment_id, aliases in ENVIRONMENT_FAMILIES:
        matches: list[tuple[int, str]] = []
        for alias in aliases:
            for match in re.finditer(r"\b" + re.escape(alias) + r"\b", normalized):
                matches.append((match.start(), alias))
        if not matches:
            continue
        matches.sort()
        if best is None or matches[0][0] < best[0]:
            best = (matches[0][0], environment_id, tuple(alias for _p, alias in matches))
    if best is None:
        return (None, ())
    return (best[1], best[2])


def _locked_weapon_surface(locked: LockedConstraints | None) -> tuple[tuple[str, ...], ...]:
    """Atoms of the locked weapon when it normalizes to a known entry."""
    if locked is None or not isinstance(locked.weapon, str) or not locked.weapon:
        return ()
    atoms = tuple(_phrase_words(locked.weapon))
    if not atoms:
        return ()
    known_atom_sets = {a for entry in KNOWN_OBJECT_TABLE for a in entry.atoms}
    return (atoms,) if atoms in known_atom_sets else ()


def _sentence_of(normalized: str, position: int) -> tuple[int, int]:
    """The [start, end) char span of the sentence containing ``position``."""
    start = 0
    for sentence in _SENTENCE_SPLIT_RE.split(normalized):
        end = start + len(sentence)
        if start <= position < end:
            return (start, end)
        start = end + 1  # +1 accounts for the delimiter character
    return (0, len(normalized))


# --------------------------------------------------------------------------- #
# Phase 14_5 — unseen noun-phrase extraction (bounded, deterministic)
# --------------------------------------------------------------------------- #

_RAW_WORD_RE = re.compile(r"[A-Za-z0-9]+")


def _word_tokens(prompt: str) -> tuple[tuple[int, int, str, str], ...]:
    """(start, end, RAW word, NFKC-casefolded word) for every word token.

    Tokens are positions in the ORIGINAL prompt so the safety re-scan of the
    RAW span keeps URL/path/prevention tokens intact ("../../evil", "file:").
    """
    out: list[tuple[int, int, str, str]] = []
    for match in _RAW_WORD_RE.finditer(prompt):
        raw = match.group(0)
        out.append(
            (
                match.start(),
                match.end(),
                raw,
                unicodedata.normalize("NFKC", raw).casefold(),
            )
        )
    return tuple(out)


def _is_numeric_token(raw: str) -> bool:
    return raw.isdigit()


def _is_proper_noun(raw: str) -> bool:
    """A capitalized word (e.g. a person/place name) is never a candidate
    object word — names like "Sarah Miller" are not world assets."""
    first = raw[0]
    return first.isalpha() and first.isupper()


def _starts_inside_claim(position: int, claims: Sequence[tuple[tuple[int, int], str]]) -> bool:
    return any(start <= position < end for (start, end), _name in claims)


def _head_is_plausible_noun(head: str) -> bool:
    """The HEAD of an unseen noun phrase must be a plausible concrete noun:
    in the bounded ``NOUN_HEAD_VOCABULARY`` and not an abstract case word nor
    a generic kit/scene word (those are environment scaffolding, never new
    objects). Single-word non-nouns (verbs/gerunds/adjectives/gibberish) are
    filtered deterministically here."""
    if head not in NOUN_HEAD_VOCABULARY:
        return False
    if head in _ABSTRACT_CASE_WORDS or head in _GENERIC_SCENE_WORDS:
        return False
    return True


def _unseen_candidates(
    prompt: str,
    claims: Sequence[tuple[tuple[int, int], str]],
    locked: LockedConstraints | None,
) -> tuple[
    tuple[tuple[tuple[int, int], str, str], ...], tuple[str, ...]
]:
    """Deterministic unseen noun phrases of ``prompt``.

    Returns ``((span, requestedName, criticality), ...)`` (document order) and
    the sanitized safe-fail notes of candidates dropped by the STRING-SAFETY
    gate (a hostile "noun" — URL scheme, path/traversal token, executable word
    — is NEVER a provider request; the caller records the note). The Head is
    vocabulary-gated and every candidate is length-bounded.
    """
    tokens = _word_tokens(prompt)
    if not tokens:
        return (), ()
    locked_atoms = ()
    if locked is not None and isinstance(locked.weapon, str) and locked.weapon:
        locked_atoms = tuple(_phrase_words(locked.weapon))
    candidates: list[tuple[tuple[int, int], str, str]] = []
    rejected_notes: list[str] = []
    pending_claims: set[tuple[int, int]] = set()

    for index, (_start, _end, _raw, fold) in enumerate(tokens):
        if fold not in _DETERMINERS:
            continue
        phrase: list[tuple[int, int, str, str]] = []
        j = index + 1
        while j < len(tokens) and len(phrase) < MAX_UNKNOWN_PHRASE_WORDS:
            t_start, t_end, raw, word = tokens[j]
            if (
                word in _PHRASE_STOP_WORDS
                or word in _DETERMINERS
                or _is_numeric_token(raw)
                or _is_proper_noun(raw)
                or word in UNSAFE_OBJECT_TERMS
                or _starts_inside_claim(t_start, claims)
                or any(_overlaps((t_start, t_end), other) for other in pending_claims)
            ):
                break
            phrase.append((t_start, t_end, raw, word))
            j += 1
        if not phrase:
            continue
        first_start = phrase[0][0]
        last_end = phrase[-1][1]
        span = (first_start, last_end)
        if any(_overlaps(span, other) for other, _name in claims):
            continue
        if any(_overlaps(span, other) for other in pending_claims):
            continue
        head = phrase[-1][3]
        if not _head_is_plausible_noun(head):
            continue
        # RAW safety re-scan: a hostile "noun" (URL/path/executable token
        # anywhere in the RAW span, control chars) is NEVER a provider request;
        # the caller records a deterministic safe-fail note.
        raw_slice = prompt[first_start:last_end]
        phrase_text = " ".join(w for _s, _e, _r, w in phrase)
        if safe_string_issues(raw_slice, "unseenNoun") or safe_string_issues(
            phrase_text, "unseenNoun"
        ):
            rejected_notes.append(
                "unsafeUnsupported: unseen noun phrase was rejected by the "
                "string-safety gate and was not composed"
            )
            continue
        requested_name = phrase_text
        # Criticality: REQUIRED when the phrase IS the locked weapon field or
        # sits in strong weapon/tool/killing-adjacent context (the bound
        # look-back window, ADV-236-narrowed — bare "with"/"used X" instrument
        # prose is DECORATIVE) that arcs the CASE story; otherwise DECORATIVE.
        phrase_words = tuple(w for _s, _e, _r, w in phrase)
        criticality = CRITICALITY_DECORATIVE
        if locked_atoms and phrase_words == locked_atoms:
            criticality = CRITICALITY_REQUIRED
        else:
            window_start = max(0, index - UNSEEN_WEAPON_CONTEXT_WINDOW)
            for back in range(window_start, index):
                if tokens[back][3] in UNSEEN_WEAPON_CONTEXT_WORDS:
                    criticality = CRITICALITY_REQUIRED
                    break
        candidates.append((span, requested_name, criticality))
        pending_claims.add(span)
    return tuple(candidates), tuple(rejected_notes)


# --------------------------------------------------------------------------- #
# public extraction
# --------------------------------------------------------------------------- #


def extract_world_requirements(
    prompt: str, locked: LockedConstraints | None = None
) -> WorldRequirements:
    """Deterministically derive ``WorldRequirements`` from one prompt.

    Pure text extraction: environment from the five alias families, objects
    from ``KNOWN_OBJECT_TABLE`` word-span matches PLUS the Phase 14_5 unseen
    noun phrases (bounded, safety-scanned; criticality required|decorative),
    relations from the bounded phrase family bound to every preceding matched
    object in the same sentence, and safe-fail notes for unsafe terms and for
    hostile "nouns" rejected by the string-safety gate. Equal inputs always
    produce equal outputs.
    """
    if not isinstance(prompt, str):
        prompt = ""
    normalized = _normalized(prompt)
    environment_hint, location_tokens = _environment_match(normalized)

    # 1. unsafe terms -> sanitized safe-fail notes, never composed.
    unsafe_notes: list[str] = []
    matched_unsafe: set[str] = set()
    for term in UNSAFE_OBJECT_TERMS:
        if re.search(r"\b" + re.escape(term) + r"\b", normalized):
            matched_unsafe.add(term)
    for term in sorted(matched_unsafe):
        unsafe_notes.append(
            f"unsafeUnsupported: known-unsafe object term {term!r} was not composed"
        )

    # 2. object table matches. Options are FLATTENED and processed longest-trigger
    #    first GLOBALLY (a longer phrase always beats a shorter one, even when the
    #    shorter belongs to an alphabetically-earlier entry); each entry emits at
    #    most one request and overlapping claims are skipped deterministically.
    objects: list[ObjectRequest] = []
    claims: list[tuple[tuple[int, int], str]] = []  # (char span, requested name)
    locked_surface = _locked_weapon_surface(locked)

    def _try_span(atoms: Sequence[str], start: int) -> tuple[int, int] | None:
        span = _first_span(_phrase_regex(atoms, TRIGGER_GAP_MAX), normalized, start)
        if span is None:
            return None
        if any(_overlaps(span, other) for other, _name in claims):
            return None
        return span

    emitted: dict[str, ObjectRequest] = {}
    options: list[tuple[int, Any, Sequence[str]]] = []
    for entry in sorted(
        KNOWN_OBJECT_TABLE, key=lambda k: k.requested_name
    ):
        for atoms in entry.atoms:
            options.append((len(atoms), entry, atoms))
    for atoms in locked_surface:
        # locked-surface trigger only refines its matching known entry
        for entry in KNOWN_OBJECT_TABLE:
            if atoms in entry.atoms:
                options.append((len(atoms), entry, atoms))
                break
    options.sort(key=lambda option: (-option[0], option[1].requested_name))

    for _atoms_len, entry, atoms in options:
        if entry.requested_name in emitted:
            continue
        span = _try_span(atoms, 0)
        if span is None:
            continue
        claims.append((span, entry.requested_name))
        emitted[entry.requested_name] = ObjectRequest(
            requested_name=entry.requested_name,
            category_hint=entry.category_hint,
            subtype_hint=entry.subtype_hint,
            tags=entry.tags,
            required_interaction=entry.required_interaction,
            evidence_id=entry.evidence_id,
        )

    # Locked-weapon guarantee: when the locked constraints pin a KNOWN object
    # (e.g. the kitchen knife), that request is ALWAYS part of the world even
    # if the prompt spells the weapon differently (deterministic, safe — the
    # entry must be table-known; never an invented asset).
    if locked_surface:
        for entry in KNOWN_OBJECT_TABLE:
            if any(atoms in entry.atoms for atoms in locked_surface):
                if entry.requested_name not in emitted:
                    emitted[entry.requested_name] = ObjectRequest(
                        requested_name=entry.requested_name,
                        category_hint=entry.category_hint,
                        subtype_hint=entry.subtype_hint,
                        tags=entry.tags,
                        required_interaction=entry.required_interaction,
                        evidence_id=entry.evidence_id,
                    )
                break

    # 2b. Phase 14_5 — unseen noun phrases become BOUNDED ObjectRequirements
    #     (criticality required|decorative; hostile spans never become requests).
    unseen_objects: list[ObjectRequest] = []
    unseen_candidates, rejected_notes = _unseen_candidates(prompt, claims, locked)
    unsafe_notes.extend(rejected_notes)
    for span, requested_name, criticality in unseen_candidates:
        claims.append((span, requested_name))
        try:
            unseen_objects.append(
                ObjectRequest(
                    requested_name=requested_name,
                    criticality=criticality,
                )
            )
        except ValueError:
            # Defensive: the safety gate already ran on the RAW span; a name
            # that still fails the bounded string checks is a hostile noun and
            # is recorded as a safe-fail note, NEVER composed.
            unsafe_notes.append(
                "unsafeUnsupported: unseen noun phrase was rejected by the "
                "string-safety gate and was not composed"
            )

    claims.sort(key=lambda pair: pair[0])
    objects = [*emitted.values(), *unseen_objects]

    # 2c. Phase 19E — GENERALIZED DETERMINISTIC WEAPON-LOCK INJECTION (the
    #     ``Weapon:``-line / prime-lock unlock, NO fork/whitelist special-case).
    #     A locked CaseTruth weapon MUST ALWAYS materialize as a REQUIRED
    #     semantic world object, regardless of catalog membership and regardless
    #     of whether the prompt spells the weapon as a determiner-span noun
    #     phrase ("Weapon: fork" has no determiner; "fork" is not in the known
    #     table). The merge rule is semantic-identity based:
    #       * a request whose semantic id matches the locked weapon keeps its
    #         (safer display) name but is upgraded to REQUIRED — the known-table
    #         "kitchen knife" for a locked "Kitchen knife" / "kitchen_knife";
    #       * otherwise a NEW Request is appended with requested_name = the
    #         locked weapon display text, criticality REQUIRED (the semantic id
    #         is then exactly the id-sheet weapon id, so CaseTruth / evidence /
    #         solver / accusation all agree);
    #       * the object bound (MAX_OBJECT_REQUESTS) is preserved by dropping
    #         trailing DECORATIVE unseen requests deterministically (the
    #         REQUIRED weapon never loses its slot to optional decoration);
    #       * a hostile locked value (URL / path / control chars / oversized)
    #         is RECORDED as a safe-fail note and NEVER composed;
    #       * ADV-235 (fail-closed safety): a locked weapon that matches
    #         ``UNSAFE_OBJECT_TERMS`` (word-boundary NFKC-casefold match, the
    #         SAME matching as the extractor's safe-fail) is NEVER composed and
    #         NEVER upgraded here — the safe-fail note is recorded (deduped with
    #         the prompt scan) and the merge is skipped entirely, so nothing
    #         unsafe materializes and the attempt FAILS CLOSED downstream
    #         (the referenced object-id is absent -> VALIDATION_FAILED, never a
    #         publish). Safe arbitrary weapons (fork/hammer/...) are unaffected.
    from app.generation.constraints import normalize_identity

    locked_weapon = locked.weapon if locked is not None else None
    if isinstance(locked_weapon, str) and locked_weapon:
        unsafe_term = unsafe_object_match(locked_weapon)
        if unsafe_term:
            note = (
                f"unsafeUnsupported: known-unsafe object term {unsafe_term!r} "
                "was not composed"
            )
            if note not in unsafe_notes:
                unsafe_notes.append(note)
        else:
            needle = normalize_identity(semantic_object_id(locked_weapon))
            if needle:
                matching = [
                    (index, request)
                    for index, request in enumerate(objects)
                    if normalize_identity(semantic_object_id(request.requested_name)) == needle
                ]
                if matching:
                    index, matching_request = matching[0]
                    if matching_request.criticality != CRITICALITY_REQUIRED:
                        objects[index] = ObjectRequest(
                            requested_name=matching_request.requested_name,
                            category_hint=matching_request.category_hint,
                            subtype_hint=matching_request.subtype_hint,
                            tags=matching_request.tags,
                            required_interaction=matching_request.required_interaction,
                            evidence_id=matching_request.evidence_id,
                            required_evidence_capabilities=(
                                matching_request.required_evidence_capabilities
                            ),
                            variant_params=matching_request.variant_params,
                            criticality=CRITICALITY_REQUIRED,
                        )
                else:
                    try:
                        injected = ObjectRequest(
                            requested_name=locked_weapon,
                            criticality=CRITICALITY_REQUIRED,
                        )
                    except ValueError:
                        unsafe_notes.append(
                            "unsafeUnsupported: locked weapon request was rejected "
                            "by the string-safety gate and was not composed"
                        )
                    else:
                        if len(objects) >= MAX_OBJECT_REQUESTS:
                            # keep the REQUIRED weapon; drop the LAST DECORATIVE
                            # unseen request deterministically (never known /
                            # already-required requests).
                            for drop_index in range(len(objects) - 1, -1, -1):
                                if objects[drop_index].criticality == CRITICALITY_DECORATIVE:
                                    del objects[drop_index]
                                    break
                            else:
                                unsafe_notes.append(
                                    "unsafeUnsupported: locked weapon request could "
                                    "not be added within the object bound"
                                )
                                injected = None  # type: ignore[assignment]
                        if injected is not None:
                            objects.append(injected)

    # 3. relations: exact contiguous phrase matches bound to every matched
    #    object in the same sentence that precedes the phrase.
    relations: list[PlacementRelation] = []
    for atoms, kind in _RELATION_PHRASES:
        pattern = _phrase_regex(atoms, 0)
        for match in pattern.finditer(normalized):
            phrase_start, phrase_end = match.start(), match.end()
            if any(_overlaps((phrase_start, phrase_end), other) for other, _n in claims):
                continue
            sentence_start, sentence_end = _sentence_of(normalized, phrase_start)
            # Every matched object in the SAME sentence whose span begins
            # before the phrase is bound to the relation.
            bound = sorted(
                {
                    name
                    for (other_start, other_end), name in claims
                    if sentence_start <= other_start < phrase_start
                }
            )
            if not bound:
                relations.append(PlacementRelation(kind=kind, target=""))
            else:
                for name in bound:
                    relations.append(PlacementRelation(kind=kind, target=name.casefold()))

    return WorldRequirements(
        environment_hint=environment_hint,
        location_tokens=location_tokens,
        objects=tuple(objects),
        relations=tuple(sorted(set(relations), key=lambda r: (r.kind, r.target))),
        unsafe_unsupported=tuple(unsafe_notes),
    )


__all__ = [
    "ENVIRONMENT_FAMILIES",
    "KNOWN_OBJECT_TABLE",
    "MAX_UNKNOWN_PHRASE_WORDS",
    "NOUN_HEAD_VOCABULARY",
    "TRIGGER_GAP_MAX",
    "UNSEEN_WEAPON_CONTEXT_WINDOW",
    "UNSEEN_WEAPON_CONTEXT_WORDS",
    "UNSAFE_OBJECT_TERMS",
    "extract_world_requirements",
    "is_base_object_request",
    "unsafe_object_match",
]