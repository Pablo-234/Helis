from __future__ import annotations

from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from helis.domain import Observation


class ObservationSource(Protocol):
    def scan(self) -> list[Observation]: ...


def stable_observation_id(source: str, external_id: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"helis:{source}:{external_id}")
