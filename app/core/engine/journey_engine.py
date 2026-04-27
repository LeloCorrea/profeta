"""Journey Engine — orquestração dos fluxos espirituais, livre de Telegram.

Responsabilidades:
- Receber tenant_id + user_id (sem objetos do Telegram)
- Resolver estado de sessão via TenantSessionStore (async)
- Orquestrar serviços legados (content_service, journey_service, verse_service)
- Retornar EngineOutput com dados normalizados

Garantias:
- Nenhum import de `telegram` ou `telegram.ext`
- Testável isoladamente com session_factory mockado
- Coexiste com bot_flows.py (legacy path) durante a migração

Fase 3: EngineFacade delega ações de conteúdo para esta classe.
Fase 5: API layer chama EngineFacade → JourneyEngine → serviços legados.
"""

from app.content_service import (
    ReflectionContent,
    build_default_prayer,
    get_or_create_explanation_content,
    get_or_create_reflection_content,
)
from app.core.contracts import EngineOutput
from app.core.engine.context_resolver import resolve_reflection, resolve_verse
from app.core.engine.state_machine import next_action_for_state
from app.core.states import JourneyState
from app.db import SessionLocal
from app.journey_service import get_active_journey
from app.session_state import TenantSessionStore
from app.verse_service import get_random_verse_for_user, save_verse_history


def _journey_dict(journey) -> dict | None:
    if journey is None:
        return None
    return {"key": journey.key, "title": journey.title, "summary": journey.summary}


class JourneyEngine:
    """Engine de jornada espiritual — sem dependências do Telegram."""

    def __init__(self, session_factory=None) -> None:
        self._sf = session_factory or SessionLocal

    # ── Entry points públicos ──────────────────────────────────────────────────

    async def verse_flow(self, tenant_id: str, user_id: str) -> EngineOutput:
        verse = await get_random_verse_for_user(user_id)
        if not verse:
            return EngineOutput(success=False, action="verse_select", error="no_verse_available")

        journey = await get_active_journey(self._sf, user_id)
        await save_verse_history(user_id, verse)

        store = TenantSessionStore(tenant_id, user_id)
        await store.set("last_verse", verse)
        await store.set_state(JourneyState.VERSE)

        return EngineOutput(
            success=True,
            action="verse_select",
            data={"verse": verse, "journey": _journey_dict(journey)},
        )

    async def continue_flow(self, tenant_id: str, user_id: str) -> EngineOutput:
        store = TenantSessionStore(tenant_id, user_id)
        state = await store.get_state()
        action = next_action_for_state(state)

        if action == "explanation_get":
            return await self._explanation_step(tenant_id, user_id)
        if action == "reflection_get":
            return await self._reflection_step(tenant_id, user_id)
        if action == "prayer_get":
            return await self._prayer_step(tenant_id, user_id)
        return await self.verse_flow(tenant_id, user_id)

    # ── Passos internos ────────────────────────────────────────────────────────

    async def _explanation_step(self, tenant_id: str, user_id: str) -> EngineOutput:
        verse = await resolve_verse(tenant_id, user_id)
        if not verse:
            return EngineOutput(success=False, action="explanation_get", error="no_verse_history")

        journey = await get_active_journey(self._sf, user_id)
        reflection = await get_or_create_explanation_content(
            self._sf,
            user_id,
            verse,
            journey_title=journey.title if journey else None,
        )

        store = TenantSessionStore(tenant_id, user_id)
        await store.set("last_explanation", reflection.as_dict())
        await store.set_state(JourneyState.EXPLANATION)

        return EngineOutput(
            success=True,
            action="explanation_get",
            data={
                "verse": verse,
                "reflection": reflection.as_dict(),
                "journey": _journey_dict(journey),
            },
        )

    async def _reflection_step(self, tenant_id: str, user_id: str) -> EngineOutput:
        verse = await resolve_verse(tenant_id, user_id)
        if not verse:
            return EngineOutput(success=False, action="reflection_get", error="no_verse_history")

        journey = await get_active_journey(self._sf, user_id)
        reflection = await get_or_create_reflection_content(
            self._sf,
            user_id,
            verse,
            journey_title=journey.title if journey else None,
        )

        store = TenantSessionStore(tenant_id, user_id)
        await store.set("last_reflection", reflection.as_dict())
        await store.set_state(JourneyState.REFLECTION)

        return EngineOutput(
            success=True,
            action="reflection_get",
            data={
                "verse": verse,
                "reflection": reflection.as_dict(),
                "journey": _journey_dict(journey),
            },
        )

    async def _prayer_step(self, tenant_id: str, user_id: str) -> EngineOutput:
        verse = await resolve_verse(tenant_id, user_id)
        if not verse:
            return EngineOutput(success=False, action="prayer_get", error="no_verse_history")

        reflection_data = await resolve_reflection(tenant_id, user_id)
        if reflection_data:
            reflection = ReflectionContent.from_dict(reflection_data)
            prayer = reflection.prayer if reflection.prayer else build_default_prayer(verse)
        else:
            prayer = build_default_prayer(verse)

        journey = await get_active_journey(self._sf, user_id)

        store = TenantSessionStore(tenant_id, user_id)
        await store.set_state(JourneyState.PRAYER)

        return EngineOutput(
            success=True,
            action="prayer_get",
            data={
                "verse": verse,
                "prayer": prayer,
                "journey": _journey_dict(journey),
            },
        )
