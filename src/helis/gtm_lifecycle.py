from __future__ import annotations

from helis.domain import VentureStage

ACTIVE_GTM_STAGES = frozenset(
    {
        VentureStage.READY_PREVIEW,
        VentureStage.LAUNCHED,
        VentureStage.MEASURING,
        VentureStage.SCALING,
    }
)


def gtm_is_active(stage: VentureStage) -> bool:
    return stage in ACTIVE_GTM_STAGES
