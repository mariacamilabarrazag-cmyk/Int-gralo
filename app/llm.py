"""Cliente de Claude y ciclo de herramientas compartido por la IA de clientes y la asistente interna."""
import logging
from typing import Callable

import anthropic

from . import config

log = logging.getLogger("llm")
_client: anthropic.Anthropic | None = None


class LLMError(RuntimeError):
    """Error con un mensaje apto para mostrar en el dashboard."""


def available() -> bool:
    return bool(config.ANTHROPIC_API_KEY)


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY or None, max_retries=3)
    return _client


def run(system: list[dict], tools: list[dict], messages: list[dict],
        execute: Callable[[str, dict], tuple[str, bool]], effort: str = "low", max_rounds: int = 10) -> str:
    """Llama a Claude, ejecuta las herramientas que pida y repite hasta obtener la respuesta final.
    `messages` se modifica en el lugar. Devuelve el último texto generado ("" si no hubo)."""
    reply = ""
    for _ in range(max_rounds):
        try:
            response = client().beta.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=16000,
                system=system,
                tools=tools,
                messages=messages,
                output_config={"effort": effort},
                # si el modelo declina una solicitud, el servidor reintenta con un modelo alternativo
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.AuthenticationError:
            raise LLMError("La clave de la API de Claude no es válida (ANTHROPIC_API_KEY).")
        except anthropic.RateLimitError:
            raise LLMError("La IA alcanzó el límite de uso; intenta de nuevo en un momento.")
        except anthropic.APIStatusError as e:
            log.error("Error de la API (%s): %s", e.status_code, e.message)
            raise LLMError(f"Error de la IA ({e.status_code}).")
        except anthropic.APIConnectionError:
            raise LLMError("Sin conexión con la IA.")

        if response.stop_reason == "refusal":
            raise LLMError("La IA no pudo responder este mensaje.")

        text = "".join(b.text for b in response.content if b.type == "text").strip()
        if text:
            reply = text
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if response.stop_reason != "tool_use" or not tool_uses:
            return reply

        messages.append({"role": "assistant", "content": response.content})
        results = []
        for tu in tool_uses:
            try:
                out, is_error = execute(tu.name, dict(tu.input or {}))
            except Exception as e:  # el error vuelve a Claude para que lo maneje
                log.exception("Error en herramienta %s", tu.name)
                out, is_error = f"Error interno: {e}", True
            results.append({"type": "tool_result", "tool_use_id": tu.id, "content": out, "is_error": is_error})
        messages.append({"role": "user", "content": results})
    return reply
