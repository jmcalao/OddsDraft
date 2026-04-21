# ═══════════════════════════════════════════════════════════════
#  🤖 SUPERBOT v5.0 — claude_ai.py
#  Análisis diario automático usando la API de Claude.
#  Corre solo en el bloque de cierre (~10pm Colombia).
# ═══════════════════════════════════════════════════════════════

import logging
from config import ANTHROPIC_API_KEY, BANKROLL, SESION
from utils  import hora_colombia
from telegram_bot import enviar_telegram

logger = logging.getLogger(__name__)


def analisis_diario_claude(historial: dict) -> bool:
    """
    Llama a Claude con el resumen del día y envía un análisis
    personalizado a Telegram.

    Requiere ANTHROPIC_API_KEY configurado en secrets.
    Si no está configurado, la función no hace nada.
    """
    if not ANTHROPIC_API_KEY:
        logger.info("🤖 Análisis Claude: ANTHROPIC_API_KEY no configurado")
        return False

    try:
        hoy         = hora_colombia().strftime("%Y-%m-%d")
        alertas_hoy = [
            a for a in historial.get("alertas", [])
            if a.get("fecha") == hoy
        ]

        if not alertas_hoy:
            logger.info("🤖 Análisis Claude: sin alertas hoy")
            return False

        # ── Construir resumen ─────────────────────────────────
        resueltas   = [a for a in alertas_hoy if a.get("estado") != "pendiente"]
        pendientes  = [a for a in alertas_hoy if a.get("estado") == "pendiente"]
        ganadas     = [a for a in resueltas   if a.get("estado") == "ganada"]
        perdidas    = [a for a in resueltas   if a.get("estado") == "perdida"]
        gan_neta_hoy = sum(a.get("ganancia_real", 0) for a in resueltas)
        wr_hoy = round(len(ganadas) / len(resueltas) * 100, 1) if resueltas else 0

        todas     = historial.get("alertas", [])
        todas_res = [a for a in todas if a.get("estado") != "pendiente"]
        draws_res = [a for a in todas_res if "draw" in a.get("tipo", "")]
        draws_w   = sum(1 for a in draws_res if a.get("estado") == "ganada")

        balance_actual = BANKROLL + sum(
            a.get("ganancia_real", 0) for a in todas_res
        )

        def _resumen_partidos(lista: list) -> str:
            lines = []
            for a in lista:
                tipo   = "Empate"
                res    = (a.get("resultado") or "").replace("-", "x") or "pendiente"
                bonus  = f" | U2.5+{a['under25_bonus']}pts" if a.get("under25_bonus") else ""
                lines.append(
                    f"  - {a['local']} vs {a['visitante']} ({a['liga']}) "
                    f"| {tipo} cuota {a['cuota']} | Score {a['score']}/100{bonus} "
                    f"| Resultado: {res} | {a['estado'].upper()}"
                )
            return "\n".join(lines) if lines else "  (ninguno)"

        prompt = f"""Eres el asistente de análisis del bot de apuestas deportivas de Marcelo.
Tu misión: analizar la jornada del día y enviarle un mensaje PERSONALIZADO, DIRECTO y ÚTIL a Marcelo via Telegram.

DATOS DE HOY ({hoy}):
- Alertas enviadas: {len(alertas_hoy)} | Resueltas: {len(resueltas)} | Pendientes: {len(pendientes)}
- Ganadas hoy: {len(ganadas)} | Perdidas hoy: {len(perdidas)} | WR hoy: {wr_hoy}%
- Ganancia/pérdida neta hoy: ${gan_neta_hoy:,} COP

DETALLE PARTIDOS HOY:
{_resumen_partidos(alertas_hoy)}

ESTADÍSTICAS ACUMULADAS TOTALES:
- Balance actual: ${balance_actual:,} COP (bankroll inicial: ${BANKROLL:,} COP)
- Empates: {draws_w}/{len(draws_res)} ganados ({round(draws_w/len(draws_res)*100,1) if draws_res else 0}% WR)

INSTRUCCIONES:
1. Empieza con "Hola Marcelo," y un emoji según el día (bien=✅ mal=⚠️ regular=📊)
2. Sé DIRECTO y HONESTO — si fue mal, dilo sin endulzar
3. Identifica patrones: ¿los empates están fallando? ¿el bono Under 2.5 ayudó?
4. Da 1-2 recomendaciones CONCRETAS y accionables
5. Comenta el balance acumulado y si estamos ganando o perdiendo
6. Cierra con una frase motivadora pero realista
7. Máximo 250 palabras — el mensaje va a Telegram
8. NO uses markdown, asteriscos ni símbolos raros — solo texto plano y emojis
"""

        r = SESION.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key":         ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
            json={
                "model":      "claude-sonnet-4-20250514",
                "max_tokens": 600,
                "messages":   [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )

        if not r.ok:
            logger.error(f"❌ Claude API: {r.status_code}: {r.text[:200]}")
            return False

        analisis = r.json()["content"][0]["text"].strip()
        sep      = "─" * 30
        msg = (
            f"🤖 <b>ANÁLISIS DE LA JORNADA — {hoy}</b>\n"
            f"{sep}\n\n"
            f"{analisis}\n\n"
            f"{sep}\n"
            f"<i>Análisis generado por Claude AI</i>"
        )
        enviar_telegram(msg)
        logger.info("✅ Análisis diario Claude enviado a Telegram")
        return True

    except Exception as e:
        logger.error(f"❌ analisis_diario_claude: {type(e).__name__}: {e}")
        return False
