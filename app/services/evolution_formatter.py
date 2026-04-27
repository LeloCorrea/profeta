"""Formata dados de evolução para mensagens exibidas ao usuário."""
from typing import Any


def format_evolution_feedback(data: dict[str, Any]) -> str:
    """Retorna mensagem motivacional em marcos de uso. Retorna '' se não há marco."""
    verse_count = data.get("verse_count") or 0
    if verse_count > 0 and verse_count % 30 == 0:
        return f"🏆 Incrível! {verse_count} versículos recebidos. Você está crescendo na Palavra!"
    if verse_count > 0 and verse_count % 7 == 0:
        return f"🌟 Uma semana de fidelidade! {verse_count} versículos recebidos."
    return ""


def get_suggested_next_action(activity_type: str) -> str:
    """Retorna sugestão de próxima ação baseada no tipo de atividade atual."""
    _suggestions = {
        "verse": "💡 Use /explicar para aprofundar a reflexão de hoje.",
        "explain": "🙏 Use /orar para orar com base neste versículo.",
        "reflection": "⭐ Use /favoritar para guardar o que tocou seu coração.",
        "prayer": "📖 Use /versiculo para receber uma nova Palavra.",
    }
    return _suggestions.get(activity_type, "")
