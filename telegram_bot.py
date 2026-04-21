# ═══════════════════════════════════════════════════════════════
#  🤖 SUPERBOT v5.0 — telegram_bot.py
#  Envío de mensajes a Telegram y formateo de alertas.
#
#  MARTINGALA: cada alerta muestra "Apuesta X/6 — $XX,XXX COP"
#  STOP: mensaje especial cuando se alcanzan 6 pérdidas seguidas
#  MEJOR SCORE: tag ⭐ cuando hay varias alertas en misma franja
# ═══════════════════════════════════════════════════════════════

import logging
from config import (
    BOT_TOKEN, CHAT_ID, SESION, BANKROLL,
    DRAW_RATE_DEFAULT, MARTINGALA_MAX_NIVEL,
    calcular_stake_martingala,
)
from utils import safe_html, _chunk_text

logger = logging.getLogger(__name__)


# ─── ENVÍO A TELEGRAM ─────────────────────────────────────────
def enviar_telegram(msg: str) -> bool:
    try:
        for chunk in _chunk_text(msg):
            r = SESION.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": CHAT_ID, "text": chunk, "parse_mode": "HTML"},
                timeout=10,
            )
            if not r.ok:
                logger.error(f"⚠️ Telegram {r.status_code}: {r.text[:100]}")
                return False
        return True
    except Exception as e:
        logger.error(f"⚠️ enviar_telegram: {type(e).__name__}: {e}")
        return False


# ─── ALERTA DE STOP MARTINGALA ────────────────────────────────
def formatear_stop_martingala(balance_actual: int) -> str:
    """
    Mensaje especial enviado UNA SOLA VEZ cuando se pierden
    6 apuestas consecutivas.
    """
    return (
        f"🛑 <b>STOP — MARTINGALA AGOTADA</b>\n\n"
        f"Se perdieron <b>{MARTINGALA_MAX_NIVEL} apuestas consecutivas</b>.\n\n"
        f"El bot sigue analizando partidos y enviando alertas,\n"
        f"pero el indicador de stake dirá STOP hasta que ganes.\n\n"
        f"💰 Balance actual: <b>${balance_actual:,} COP</b>\n"
        f"(Bankroll inicial: ${BANKROLL:,} COP)\n\n"
        f"🔄 La martingala se reinicia automáticamente cuando "
        f"registre tu próxima victoria.\n\n"
        f"⚠️ <i>Revisa tu estrategia antes de continuar apostando.</i>"
    )


# ─── ALERTA EMPATE ────────────────────────────────────────────
def formatear_alerta_draw(
    liga: str,
    local: str,
    visitante: str,
    score: int,
    cuota_loc: float | None,
    cuota_emp: float,
    cuota_vis: float | None,
    prob_real: int,
    prob_imp: int,
    num_bm: int,
    hora_col: str,
    razones: list[str],
    draw_rate: float,
    # ── Martingala ──────────────────────────────────────────────
    nivel_martingala: int = 1,
    martingala_activa: bool = True,
    # ── Under 2.5 bonus ─────────────────────────────────────────
    under25_bonus: int = 0,
    cuota_under25: float | None = None,
    # ── Sugerencia de mejor score ────────────────────────────────
    es_mejor_score: bool = False,
) -> str:
    try:
        stake    = calcular_stake_martingala(nivel_martingala)
        ganancia = round(stake * cuota_emp) - stake
        valor    = round(prob_real - prob_imp, 1)
        emoji    = "🔥" if score >= 85 else "🟢"
        barra    = "█" * round(score / 10) + "░" * (10 - round(score / 10))
        dr_tag   = (
            "(conocida)" if draw_rate != DRAW_RATE_DEFAULT
            else "(promedio global)"
        )
        razones_txt = "\n".join(f"  · {safe_html(r)}" for r in razones)
        mejor_tag   = " ⭐ MEJOR SCORE" if es_mejor_score else ""

        # ── Sección Martingala ─────────────────────────────────
        if not martingala_activa:
            mart_txt = (
                f"\n🛑 <b>MARTINGALA DETENIDA</b> — revisá tu estrategia\n"
            )
        elif nivel_martingala == 1:
            mart_txt = (
                f"\n🎯 <b>Apuesta 1/{MARTINGALA_MAX_NIVEL}</b> "
                f"— inicio de ciclo\n"
            )
        else:
            mart_txt = (
                f"\n⚠️ <b>Apuesta {nivel_martingala}/{MARTINGALA_MAX_NIVEL}</b> "
                f"— Martingala activa "
                f"({nivel_martingala - 1} pérdida(s) consecutiva(s))\n"
            )

        # ── Sección Under 2.5 bonus ────────────────────────────
        seccion_u25 = ""
        if under25_bonus > 0 and cuota_under25:
            seccion_u25 = (
                f"\n⚡ <b>UNDER 2.5 — BONO +{under25_bonus}pts</b>\n"
                f"  Cuota Under 2.5: <b>{cuota_under25}</b>\n"
                f"  Partido candidato a terminar con 0, 1 o 2 goles.\n"
            )

        cuota_loc_str = str(cuota_loc) if cuota_loc else "—"
        cuota_vis_str = str(cuota_vis) if cuota_vis else "—"

        return (
            f"{emoji} <b>ALERTA EMPATE{mejor_tag}</b>\n\n"
            f"⚽ <b>{safe_html(local)} vs {safe_html(visitante)}</b>\n"
            f"🏆 {safe_html(liga)} | 🕐 {safe_html(hora_col)} Col\n\n"
            f"📊 <b>Score empate: {score}/100</b>\n"
            f"<code>{barra}</code>\n\n"
            f"💰 Cuotas: {safe_html(cuota_loc_str)} | "
            f"<b>{safe_html(str(cuota_emp))}</b> | "
            f"{safe_html(cuota_vis_str)}\n"
            f"         Local  |  Empate  | Visita\n\n"
            f"📈 EV: {'+' if valor > 0 else ''}{valor}%\n"
            f"🎲 Prob real: {prob_real}% vs implícita: {prob_imp}%\n"
            f"📉 Draw rate liga: {round(draw_rate*100)}% {dr_tag}\n"
            f"👥 Consenso: {num_bm} bookmakers\n\n"
            f"<b>Razones del score:</b>\n{razones_txt}\n"
            f"{seccion_u25}"
            f"{mart_txt}\n"
            f"💵 <b>Apostar: ${stake:,} COP</b>\n"
            f"🎯 Ganancia potencial: +${ganancia:,} COP\n\n"
            f"⚠️ Juega con responsabilidad"
        )

    except Exception as e:
        logger.error(f"❌ formatear_alerta_draw: {e}")
        return ""


# ─── MENSAJE SIN ALERTAS ──────────────────────────────────────
def formatear_sin_alertas(
    bloque: str,
    ahora: str,
    partidos_vistos: int,
    ligas_vistas: set,
    partidos_filtrados: int,
    mejor_partido: str,
    score_minimo: int,
    rapidapi_h2h_calls: int,
    stats_txt: str,
) -> str:
    ra = (
        f"activo ✅ ({rapidapi_h2h_calls} H2H)"
        if rapidapi_h2h_calls > 0 else "inactivo ⚠️"
    )
    filtrados_txt = (
        f"\n🚫 {partidos_filtrados} descartados por favorito claro"
        if partidos_filtrados else ""
    )
    mejor_txt = (
        f"\n\n🏅 <b>Mejor partido (no superó {score_minimo}):</b>\n"
        f"{safe_html(mejor_partido)}"
        if mejor_partido else ""
    )
    return (
        f"🔍 <b>SuperBot v5.0 — {safe_html(bloque)}</b>\n\n"
        f"📅 {safe_html(ahora)} (Colombia)\n"
        f"📊 Analicé <b>{partidos_vistos} partido(s)</b> en "
        f"<b>{len(ligas_vistas)} liga(s)</b>"
        f"{safe_html(filtrados_txt)}"
        f"{mejor_txt}\n\n"
        f"📡 RapidAPI H2H: {ra}\n"
        f"❌ Ninguno superó score {score_minimo}\n\n"
        f"{stats_txt}\n\n"
        f"⏰ Próxima revisión automática pronto! 👀"
    )


# ─── RESUMEN CON ALERTAS ──────────────────────────────────────
def formatear_resumen_alertas(
    bloque: str,
    ahora: str,
    partidos_vistos: int,
    ligas_vistas: set,
    log_draws: list,
    alertas_enviadas: int,
    score_min_draw: int,
    rapidapi_h2h_calls: int,
    stats_txt: str,
) -> str:
    ra = (
        f"activo ✅ ({rapidapi_h2h_calls} H2H)"
        if rapidapi_h2h_calls > 0 else "inactivo ⚠️"
    )
    detalle = ""
    if log_draws:
        detalle += "\n🟢 <b>EMPATES:</b>\n"
        for loc, vis, lig, sd, hc, bonus, nivel in log_draws:
            bonus_tag = f" ⚡U2.5+{bonus}" if bonus else ""
            mart_tag  = f" | M:{nivel}/{MARTINGALA_MAX_NIVEL}"
            detalle  += (
                f"  · {safe_html(loc)} vs {safe_html(vis)}\n"
                f"    {safe_html(lig)} | {safe_html(hc)} Col "
                f"| Score:{sd}/100{safe_html(bonus_tag)}{safe_html(mart_tag)}\n"
            )
    return (
        f"✅ <b>SuperBot v5.0 — {safe_html(bloque)}</b>\n\n"
        f"📅 {safe_html(ahora)} (Colombia)\n"
        f"📊 Analicé <b>{partidos_vistos} partido(s)</b> en "
        f"<b>{len(ligas_vistas)} liga(s)</b>\n"
        f"📡 RapidAPI H2H: {ra}\n"
        f"<b>Alertas enviadas:</b>{detalle}\n"
        f"🟢 {alertas_enviadas} empate(s)\n"
        f"✅ Score mínimo: {score_min_draw}\n\n"
        f"{stats_txt}"
    )
