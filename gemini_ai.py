# ═══════════════════════════════════════════════════════════════
#  🤖 SUPERBOT v5.0 — gemini_ai.py
#
#  Dos funciones:
#  1. analizar_partido_gemini()  — se llama por cada alerta,
#     devuelve 2-3 líneas de contexto para agregar al mensaje.
#  2. analisis_diario_gemini()   — resumen nocturno (bloque cierre).
#
#  API: Google Gemini 2.0 Flash (gratuita, 1500 requests/día)
#  Secret requerido: GEMINI_API_KEY
#  Obtener key: https://aistudio.google.com/app/apikey
# ═══════════════════════════════════════════════════════════════

import logging
from config import SESION, BANKROLL
from utils  import hora_colombia

logger = logging.getLogger(__name__)

GEMINI_API_KEY = __import__('os').environ.get("GEMINI_API_KEY", "")
GEMINI_URL     = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.0-flash:generateContent"
)


def _llamar_gemini(prompt: str, max_tokens: int = 200) -> str | None:
    """Llama a Gemini API y retorna el texto generado, o None si falla."""
    if not GEMINI_API_KEY:
        return None
    try:
        r = SESION.post(
            GEMINI_URL,
            params={"key": GEMINI_API_KEY},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "maxOutputTokens": max_tokens,
                    "temperature":     0.4,
                },
            },
            timeout=20,
        )
        if r.ok:
            return (
                r.json()
                .get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
                .strip()
            )
        logger.warning(f"⚠️ Gemini {r.status_code}: {r.text[:120]}")
    except Exception as e:
        logger.warning(f"⚠️ Gemini: {type(e).__name__}: {e}")
    return None


# ─── ANÁLISIS POR ALERTA ──────────────────────────────────────
def analizar_partido_gemini(
    local: str,
    visitante: str,
    liga: str,
    score: int,
    cuota_emp: float,
    cuota_loc: float | None,
    cuota_vis: float | None,
    draw_rate: float,
    under25_bonus: int,
    razones: list[str],
) -> str:
    """
    Genera 2-3 líneas de análisis contextual para adjuntar a la alerta.
    Si Gemini no está disponible retorna cadena vacía (la alerta se envía igual).
    """
    if not GEMINI_API_KEY:
        return ""

    razon_txt = " | ".join(razones[:4]) if razones else "sin datos adicionales"
    u25_txt   = f"Tiene bono Under 2.5 ({under25_bonus} pts extra)." if under25_bonus else ""
    eq_txt    = (
        f"Cuotas: local {cuota_loc} / empate {cuota_emp} / visita {cuota_vis}."
        if cuota_loc and cuota_vis
        else f"Cuota empate: {cuota_emp}."
    )

    prompt = f"""Eres un analista de apuestas deportivas experto en empates de fútbol.
Analiza este partido en máximo 2 oraciones cortas y directas.
NO uses asteriscos, NO uses markdown, solo texto plano con emojis si quieres.

Partido: {local} vs {visitante} ({liga})
Score del bot: {score}/100
Draw rate histórico de la liga: {round(draw_rate*100)}%
{eq_txt}
{u25_txt}
Factores clave: {razon_txt}

Responde en español. Máximo 2 oraciones. Sé directo: ¿vale la pena este empate?"""

    resultado = _llamar_gemini(prompt, max_tokens=120)
    if resultado:
        return f"\n🤖 <i>{resultado}</i>"
    return ""


# ─── ANÁLISIS DIARIO (BLOQUE CIERRE) ─────────────────────────
def analisis_diario_gemini(historial: dict) -> bool:
    """
    Envía un resumen de la jornada a Telegram usando Gemini.
    Corre solo en el bloque de cierre (~10pm Colombia).
    """
    if not GEMINI_API_KEY:
        logger.info("🤖 Análisis Gemini: GEMINI_API_KEY no configurado")
        return False

    from telegram_bot import enviar_telegram

    try:
        hoy         = hora_colombia().strftime("%Y-%m-%d")
        alertas_hoy = [
            a for a in historial.get("alertas", [])
            if a.get("fecha") == hoy and a.get("tipo") == "draw"
        ]

        if not alertas_hoy:
            logger.info("🤖 Análisis Gemini: sin alertas hoy")
            return False

        resueltas  = [a for a in alertas_hoy if a.get("estado") != "pendiente"]
        ganadas    = [a for a in resueltas   if a.get("estado") == "ganada"]
        perdidas   = [a for a in resueltas   if a.get("estado") == "perdida"]
        pendientes = [a for a in alertas_hoy if a.get("estado") == "pendiente"]
        gan_neta   = sum(a.get("ganancia_real", 0) for a in resueltas)
        wr_hoy     = round(len(ganadas)/len(resueltas)*100, 1) if resueltas else 0

        # Estadísticas acumuladas solo draws
        todas_draw = [
            a for a in historial["alertas"]
            if a.get("tipo") == "draw" and a.get("estado") != "pendiente"
        ]
        total_w    = sum(1 for a in todas_draw if a["estado"] == "ganada")
        wr_total   = round(total_w/len(todas_draw)*100, 1) if todas_draw else 0
        balance    = BANKROLL + sum(
            a.get("ganancia_real", 0) for a in todas_draw
        )

        def _linea(a):
            res   = (a.get("resultado") or "").replace("-","x") or "pend."
            bonus = f" U2.5+{a['under25_bonus']}pts" if a.get("under25_bonus") else ""
            return (
                f"{a['local']} vs {a['visitante']} "
                f"score:{a['score']}{bonus} cuota:{a['cuota']} → {res} {a['estado'].upper()}"
            )

        detalle = "\n".join(_linea(a) for a in alertas_hoy)

        prompt = f"""Eres el asistente de análisis del bot de apuestas deportivas de Marcelo.
Hoy es {hoy}. Escribe un mensaje DIRECTO para Telegram (máximo 220 palabras).
Sin markdown, sin asteriscos. Usa emojis. Texto plano.

PARTIDOS DE HOY:
{detalle}

RESUMEN HOY: {len(ganadas)}W / {len(perdidas)}L / {len(pendientes)} pendientes | WR {wr_hoy}% | neto ${gan_neta:,} COP
ACUMULADO: {total_w}/{len(todas_draw)} empates ({wr_total}% WR) | Balance ${balance:,} COP (inicio ${BANKROLL:,})

Instrucciones:
1. Saluda a Marcelo con emoji según día (bien=✅ mal=⚠️)
2. Comenta brevemente qué pasó hoy
3. ¿Hay patrón? ¿Cierto score funciona mejor?
4. Una recomendación concreta (ej: subir score mínimo, ligas a evitar)
5. Cierra con frase motivadora breve
No inventes datos que no están arriba."""

        analisis = _llamar_gemini(prompt, max_tokens=400)
        if not analisis:
            return False

        sep = "─" * 28
        msg = (
            f"🤖 <b>ANÁLISIS JORNADA — {hoy}</b>\n"
            f"{sep}\n\n"
            f"{analisis}\n\n"
            f"{sep}\n"
            f"<i>Análisis por Gemini AI</i>"
        )
        enviar_telegram(msg)
        logger.info("✅ Análisis diario Gemini enviado")
        return True

    except Exception as e:
        logger.error(f"❌ analisis_diario_gemini: {type(e).__name__}: {e}")
        return False
        
