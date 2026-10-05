import hashlib
import re
from dataclasses import dataclass

from creativo_api.errors import AppError

_CHILD = [
    re.compile(r"\b(?:csam|child\s+porn(?:ography)?)\b", re.I),
    re.compile(r"\b(?:loli|lolita|shota)\b", re.I),
    re.compile(
        r"\b(?:preteen|underage|minor|child|children|kid|kids|toddler)\b.{0,48}"
        r"\b(?:nude|naked|sex|sexual|porn|explicit)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:nude|naked|sex|sexual|porn|explicit)\b.{0,48}"
        r"\b(?:preteen|underage|minor|child|children|kid|kids|toddler)\b",
        re.I,
    ),
]

_SEXUAL = [
    re.compile(r"\b(?:pornography|porn|xxx|hentai|nsfw|hardcore)\b", re.I),
    re.compile(r"\b(?:nude|nudes|naked|topless)\b", re.I),
    re.compile(r"\b(?:explicit sex|having sex|sexual intercourse|erotic)\b", re.I),
]

_ABUSE = [
    re.compile(r"\b(?:rape|raping|sexual assault)\b", re.I),
]


@dataclass(frozen=True)
class Rule:
    id: str
    category: str
    pattern: re.Pattern[str]
    sfw_only: bool = False


@dataclass(frozen=True)
class Decision:
    action: str
    category: str | None = None
    rule_id: str | None = None
    source: str = "rules"


def _rules() -> list[Rule]:
    rows = [Rule(f"child_{index}", "child_sexual", pattern) for index, pattern in enumerate(_CHILD)]
    rows += [
        Rule(f"abuse_{index}", "sexual_violence", pattern) for index, pattern in enumerate(_ABUSE)
    ]
    rows += [
        Rule(f"sexual_{index}", "sexual_content", pattern, sfw_only=True)
        for index, pattern in enumerate(_SEXUAL)
    ]
    return rows


RULES = _rules()


class AllowClassifier:
    """Stand-in for a dedicated classifier. Rules remain the blocking authority."""

    async def classify(self, prompt: str) -> Decision:
        return Decision(action="allow", source="classifier")


def prompt_hash(prompt: str) -> str:
    normalized = " ".join(prompt.lower().split())
    return hashlib.sha256(normalized.encode()).hexdigest()


async def decide(
    prompt: str, *, sfw_only: bool, classifier: AllowClassifier | None = None
) -> Decision:
    for rule in RULES:
        if rule.sfw_only and not sfw_only:
            continue
        if rule.pattern.search(prompt):
            return Decision(action="block", category=rule.category, rule_id=rule.id, source="rules")
    model = classifier or AllowClassifier()
    verdict = await model.classify(prompt)
    if verdict.action == "block":
        return Decision(
            action="block",
            category=verdict.category or "classifier",
            source="classifier",
            rule_id=verdict.rule_id,
        )
    return Decision(action="allow", source="rules")


def rejection_error(generation_id: str, category: str | None) -> AppError:
    return AppError(
        "content_rejected",
        "This request was blocked by the safety policy.",
        422,
        {"generation_id": generation_id, "category": category},
    )
