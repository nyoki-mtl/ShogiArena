"""SPSA game participation identity contract。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from shogiarena._core.shared.kernel.json_coercion import coerce_json_object_serialized
from shogiarena._core.shared.kernel.json_types import JsonObject
from shogiarena._core.shared.kernel.participation_records import GameParticipationRecord

SPSA_IDENTITY_KEY = "spsa_identity"
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class SpsaParticipationIdentity(BaseModel):
    """一局をoptimizer run、update、pair、attemptへ結び付けるidentity。"""

    run_id: NonEmptyStr
    update_idx: int = Field(ge=1)
    pair_id: NonEmptyStr
    attempt_id: NonEmptyStr
    observation_kind: Literal["SPSA", "LTC"]

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SpsaParticipationIdentityError(ValueError):
    """Participation identityが欠落または矛盾している。"""


def attach_spsa_participation_identity(
    records: Sequence[GameParticipationRecord],
    *,
    identity: SpsaParticipationIdentity,
) -> tuple[GameParticipationRecord, ...]:
    """両sideのparticipationへ同一identityを埋め込む。"""

    if {record.role for record in records} != {"black", "white"} or len(records) != 2:
        raise SpsaParticipationIdentityError("SPSA participation must contain exactly black and white records")
    payload = identity.model_dump(mode="json")
    attached: list[GameParticipationRecord] = []
    for record in records:
        extra = coerce_json_object_serialized(record.extra or {}, field_name="participation.extra")
        extra[SPSA_IDENTITY_KEY] = payload
        attached.append(record.model_copy(update={"run_id": identity.run_id, "extra": extra}))
    return tuple(attached)


def parse_spsa_participation_identity(
    extras: Sequence[Mapping[str, object]],
) -> SpsaParticipationIdentity:
    """両sideの保存済みextraから一致するidentityをstrict parseする。"""

    if len(extras) != 2:
        raise SpsaParticipationIdentityError("SPSA game must have exactly two participation records")
    identities: list[SpsaParticipationIdentity] = []
    for extra in extras:
        raw = extra.get(SPSA_IDENTITY_KEY)
        try:
            identities.append(SpsaParticipationIdentity.model_validate(raw))
        except ValidationError as exc:
            raise SpsaParticipationIdentityError("SPSA participation identity is missing or invalid") from exc
    if identities[0] != identities[1]:
        raise SpsaParticipationIdentityError("Black and white SPSA participation identities do not match")
    return identities[0]


def identity_extra(identity: SpsaParticipationIdentity) -> JsonObject:
    """Return a serialized extra payload for tests and boundary adapters."""

    return {SPSA_IDENTITY_KEY: identity.model_dump(mode="json")}


__all__ = [
    "SPSA_IDENTITY_KEY",
    "SpsaParticipationIdentity",
    "SpsaParticipationIdentityError",
    "attach_spsa_participation_identity",
    "identity_extra",
    "parse_spsa_participation_identity",
]
