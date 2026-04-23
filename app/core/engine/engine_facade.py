"""Engine Facade — ponto de entrada único para todas as ações do engine.

Fase 1: delega diretamente aos serviços legados (verse_service, journey_service).
        Coexiste com chamadas diretas de bot_flows.py; bot.py não é alterado.
Fase 3: ações de conteúdo (explanation, reflection, prayer, continue) são
        delegadas ao JourneyEngine (importado via lazy import para evitar ciclos).
Fase 5: API layer e bot adapter passam a chamar engine.execute() exclusivamente.
"""

from app.core.contracts import EngineInput, EngineOutput
from app.db import SessionLocal
from app.journey_service import get_active_journey, start_journey
from app.verse_service import (
    get_last_verse_for_user,
    get_random_verse_for_user,
    save_verse_history,
)


class EngineFacade:
    """Roteia EngineInput para o handler correto e retorna EngineOutput.

    Instanciação:
        engine = EngineFacade()           # usa SessionLocal padrão
        engine = EngineFacade(sf)         # injeta session factory (testes)
    """

    def __init__(self, session_factory=None) -> None:
        self._sf = session_factory or SessionLocal

    async def execute(self, inp: EngineInput) -> EngineOutput:
        handlers = {
            # ── verse ─────────────────────────────────────────────────────────
            "verse_select": self._verse_select,
            "verse_save_history": self._verse_save_history,
            "verse_get_last": self._verse_get_last,
            # ── journey ───────────────────────────────────────────────────────
            "journey_get_active": self._journey_get_active,
            "journey_start": self._journey_start,
            # ── content (Fase 3 — lazy-import JourneyEngine) ─────────────────
            "explanation_get": self._explanation_get,
            "reflection_get": self._reflection_get,
            "prayer_get": self._prayer_get,
            "continue": self._continue,
        }
        handler = handlers.get(inp.action)
        if handler is None:
            return EngineOutput(
                success=False,
                action=inp.action,
                error=f"unknown_action:{inp.action}",
            )
        try:
            return await handler(inp)
        except Exception as exc:
            return EngineOutput(success=False, action=inp.action, error=str(exc))

    # ── Verse ──────────────────────────────────────────────────────────────────

    async def _verse_select(self, inp: EngineInput) -> EngineOutput:
        verse = await get_random_verse_for_user(inp.user_id)
        if not verse:
            return EngineOutput(success=False, action=inp.action, error="no_verse_available")
        return EngineOutput(success=True, action=inp.action, data={"verse": verse})

    async def _verse_save_history(self, inp: EngineInput) -> EngineOutput:
        verse = inp.payload.get("verse")
        if not verse:
            return EngineOutput(success=False, action=inp.action, error="verse_required_in_payload")
        await save_verse_history(inp.user_id, verse)
        return EngineOutput(success=True, action=inp.action, data={})

    async def _verse_get_last(self, inp: EngineInput) -> EngineOutput:
        verse = await get_last_verse_for_user(inp.user_id)
        if not verse:
            return EngineOutput(success=False, action=inp.action, error="no_verse_history")
        return EngineOutput(success=True, action=inp.action, data={"verse": verse})

    # ── Journey ────────────────────────────────────────────────────────────────

    async def _journey_get_active(self, inp: EngineInput) -> EngineOutput:
        journey = await get_active_journey(self._sf, inp.user_id)
        return EngineOutput(
            success=True,
            action=inp.action,
            data={
                "journey": (
                    {"key": journey.key, "title": journey.title, "summary": journey.summary}
                    if journey
                    else None
                )
            },
        )

    async def _journey_start(self, inp: EngineInput) -> EngineOutput:
        key = inp.payload.get("journey_key")
        if not key:
            return EngineOutput(success=False, action=inp.action, error="journey_key_required")
        journey = await start_journey(self._sf, inp.user_id, key)
        if not journey:
            return EngineOutput(success=False, action=inp.action, error=f"unknown_journey:{key}")
        return EngineOutput(
            success=True,
            action=inp.action,
            data={"journey": {"key": journey.key, "title": journey.title}},
        )

    # ── Content — delegado ao JourneyEngine (Fase 3) ───────────────────────────
    # Lazy import evita importação circular e garante que Fase 1 funciona mesmo
    # antes de journey_engine.py existir (ImportError é convertido em fallback).

    async def _explanation_get(self, inp: EngineInput) -> EngineOutput:
        try:
            from app.core.engine.journey_engine import JourneyEngine
        except ImportError:
            return EngineOutput(success=False, action=inp.action, error="journey_engine_not_available")
        return await JourneyEngine(self._sf)._explanation_step(inp.tenant_id, inp.user_id)

    async def _reflection_get(self, inp: EngineInput) -> EngineOutput:
        try:
            from app.core.engine.journey_engine import JourneyEngine
        except ImportError:
            return EngineOutput(success=False, action=inp.action, error="journey_engine_not_available")
        return await JourneyEngine(self._sf)._reflection_step(inp.tenant_id, inp.user_id)

    async def _prayer_get(self, inp: EngineInput) -> EngineOutput:
        try:
            from app.core.engine.journey_engine import JourneyEngine
        except ImportError:
            return EngineOutput(success=False, action=inp.action, error="journey_engine_not_available")
        return await JourneyEngine(self._sf)._prayer_step(inp.tenant_id, inp.user_id)

    async def _continue(self, inp: EngineInput) -> EngineOutput:
        try:
            from app.core.engine.journey_engine import JourneyEngine
        except ImportError:
            return EngineOutput(success=False, action=inp.action, error="journey_engine_not_available")
        return await JourneyEngine(self._sf).continue_flow(inp.tenant_id, inp.user_id)


# Instância singleton — mesma convenção de CURRENT_TENANT em config.py.
# Testes podem instanciar EngineFacade(session_factory=mock_sf) sem afetar este singleton.
engine = EngineFacade()
