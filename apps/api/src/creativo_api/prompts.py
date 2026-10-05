from dataclasses import dataclass
from typing import Protocol

from creativo_api.policy import Decision


@dataclass(frozen=True)
class PromptIR:
    text: str
    data: dict


class PromptEnhancer(Protocol):
    async def enhance(self, prompt: str) -> PromptIR: ...


class PassthroughEnhancer:
    """Keeps the user's words. A richer IR can grow once a second model exists."""

    async def enhance(self, prompt: str) -> PromptIR:
        text = prompt.strip()
        return PromptIR(
            text=text,
            data={
                "schema_version": 1,
                "text": text,
                "subject": None,
                "environment": None,
                "camera": None,
                "lighting": None,
                "motion": None,
                "style": None,
            },
        )


@dataclass(frozen=True)
class PreparedPrompt:
    final: str | None
    ir: dict | None


async def prepare_prompt(
    decision: Decision,
    enhancer: PromptEnhancer,
    prompt: str,
) -> PreparedPrompt:
    if decision.action == "block":
        return PreparedPrompt(final=None, ir=None)
    enriched = await enhancer.enhance(prompt)
    return PreparedPrompt(final=enriched.text, ir=enriched.data)
