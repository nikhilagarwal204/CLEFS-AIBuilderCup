"""
Expectations DSL for eval scenarios (03 spec 5.4).

A scenario's `expectations` is one JSON object; every key is optional:

    must_call        [{name: str | [str], args_match: {arg: matcher}, result_match: {field: matcher}}]
    must_not_call    [str | {name, args_match}]
    must_escalate    {reason: str | [str], urgency: str | [str]} | true
    must_not_escalate true
    must_contain_any [str]                 at least one appears (case-insensitive)
    must_not_contain [str]                 none appears (case-insensitive)
    language         "en" | "ar" | "ru" | "hi" | ... | [one of]
    max_sentences    int
    no_unlisted_prices true                every amount in the hotel's currency is in the menu or KB price set
    emergency_numbers [str] | {CC: [str]}  every number appears as a whole token; a map is keyed by the
                                           hotel's country (ISO 3166 alpha-2), and a country it does not
                                           list fails: the scenario cannot tell the right number there
    grounded_in_kb   true | {entry_id}     the LLM judge checks faithfulness to answer_en
    bound_room       str                   every side effect landed on this room
    unknown_or_escalate true               escalates (unknown_answer) or says it does not know
    rubric           [str]                 extra LLM-judge criteria

A matcher is a scalar (equal, case-insensitive for text, numeric when both
sides parse), a list (any of), or {contains | gte | lte | any: [...]}.

Parsing is strict (pydantic, extra keys refused) so a typo in a YAML file
fails the startup sync instead of silently testing nothing.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ─── Models ───


class CallExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | list[str]
    args_match: dict[str, Any] = Field(default_factory=dict)
    # Checked only when the runner returned the executor's output (the
    # dry-run executor's severity, SLA, bound room).
    result_match: dict[str, Any] = Field(default_factory=dict)

    def names(self) -> list[str]:
        return [self.name] if isinstance(self.name, str) else list(self.name)


class EscalateExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | list[str] | None = None
    urgency: str | list[str] | None = None


class KbGrounding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entry_id: int | None = None


class Expectations(BaseModel):
    model_config = ConfigDict(extra="forbid")
    must_call: list[CallExpectation] = Field(default_factory=list)
    must_not_call: list[CallExpectation] = Field(default_factory=list)
    must_escalate: EscalateExpectation | None = None
    must_not_escalate: bool = False
    must_contain_any: list[str] = Field(default_factory=list)
    must_not_contain: list[str] = Field(default_factory=list)
    language: str | list[str] | None = None
    max_sentences: int | None = Field(default=None, ge=1, le=50)
    no_unlisted_prices: bool = False
    emergency_numbers: list[str] | dict[str, list[str]] = Field(default_factory=list)
    grounded_in_kb: KbGrounding | None = None
    bound_room: str | None = None
    unknown_or_escalate: bool = False
    rubric: list[str] = Field(default_factory=list)

    @field_validator("must_not_call", mode="before")
    @classmethod
    def _names_to_calls(cls, v: Any) -> Any:
        # must_not_call: ["place_order"] is the common short form.
        return [{"name": x} if isinstance(x, str) else x for x in (v or [])]

    @field_validator("emergency_numbers")
    @classmethod
    def _countries(cls, v: Any) -> Any:
        if isinstance(v, dict):
            bad = [k for k in v if not (len(k) == 2 and k.isalpha())]
            if bad:
                raise ValueError(f"emergency_numbers keys are ISO country codes, got {bad}")
            return {k.upper(): list(n) for k, n in v.items()}
        return v

    @field_validator("must_escalate", mode="before")
    @classmethod
    def _escalate_true(cls, v: Any) -> Any:
        if v is True:
            return {}
        return None if v is False else v

    @field_validator("grounded_in_kb", mode="before")
    @classmethod
    def _grounded_true(cls, v: Any) -> Any:
        if v is True:
            return {}
        return None if v is False else v

    def numbers_for(self, country: str | None) -> list[str] | None:
        """The emergency numbers to expect at a hotel in `country`: the list
        itself, or the map's entry (None when the map has no entry)."""
        if isinstance(self.emergency_numbers, dict):
            return self.emergency_numbers.get((country or "").strip().upper())
        return list(self.emergency_numbers)

    def languages(self) -> list[str]:
        if self.language is None:
            return []
        return [self.language] if isinstance(self.language, str) else list(self.language)

    def needs_judge(self) -> bool:
        return bool(self.rubric or self.grounded_in_kb is not None or self.unknown_or_escalate)


def parse(raw: Any) -> Expectations:
    """Validate an expectations object (raises pydantic.ValidationError)."""
    return Expectations.model_validate(raw or {})
