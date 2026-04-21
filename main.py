# ═══════════════════════════════════════════════════════════════
#  🤖 SUPERBOT v5.0 — main.py
#
#  Orquestador principal. Una sola función main(), limpia.
#
#  CAMBIOS vs v4.6:
#  ✅ Código dividido en módulos (config, utils, scoring, etc.)
#  ✅ Under 3.5 eliminado — Under 2.5 integrado como BONUS al draw
#  ✅ Un solo tipo de alerta: EMPATE (con bono U2.5 si aplica)
#  ✅ Emoji ⭐ al partido con mejor score cuando hay varios en la misma franja
#  ✅ Duplicados corregidos — una sola definición de cada función
#  ✅ Typo "Draft" corregido a "Empate" en Google Sheets
#  ✅ Nuevo campo under25_bonus en historial
#
#  PENDIENTE (v5.1):
#  TODO: Implementar Martingala x6 (reemplazar calcular_stake)
# ═══════════════════════════════════════════════════════════════

import sys
import time
import logging

from config       import (
    validar_configuracion, SCORE_MIN_DRAW, MIN_BOOKMAKERS_DRAW,
    PRE_THRESHOLD, RAPIDAPI_MAX_H2H_POR_RUN, RAPIDAPI_KEY,
)
from utils        import (
    hora_colombia, get_bloque_actual, es_bloque_cierre,
    build_alert_id, safe_html,
)
from fetcher      import (
    get_todos_los_partidos, filtrar_partidos_hoy,
    extraer_cuotas_h2h, extraer_cuotas_under25,
    get_draw_rate, get_under25_rate, nombre_liga,
)
from rapidapi     import (
    get_fixtures_hoy_rapidapi, buscar_fixture_rapidapi,
    get_h2h_rapidapi, calcular_modificadores_rapidapi,
)
from scoring      import calcular_score_draw, calcular_bonus_under25
from historial    import (
    cargar_historial, guardar_historial, registrar_alerta,
    actualizar_resultados, calcular_stats_detalladas, formatear_reporte_stats,
)
from sheets       import sincronizar_google_sheets
from telegram_bot import (
    enviar_telegram, formatear_alerta_draw,
    formatear_sin_alertas, formatear_resumen_alertas,
)
from claude_ai    import analisis_diario_claude
from backup       import backup_historial_github
from utils        import hora_local_col

# ─── LOGGING ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════
def main():
    try:
        # ── Validación y setup ────────────────────────────────
        validar_configuracion()
        bloque = get_bloque_actual()
        ahora  = hora_colombia().strftime("%Y-%m-%d %H:%M")

        # ── Cargar historial y verificar resultados pendientes ─
        historial    = cargar_historial()
        existing_ids = {a.get("id") for a in historial.get("alertas", [])}

        actualizadas = actualizar_resultados(historial)
        if actualizadas > 0:
            guardar_historial(historial)
            logger.info(f"📊 {actualizadas} alerta(s) actualizadas con resultado")
            sincronizar_google_sheets(historial)

        # ── Fuera de horario ──────────────────────────────────
        if bloque is None:
            logger.info(f"⏸ Fuera de horario — {ahora} Colombia")
            enviar_telegram(
                f"⏸ <b>SuperBot v5.0 fuera de horario</b>\n"
                f"📅 {safe_html(ahora)} (Colombia)\n\n"
                f"Próxima revisión automática a las 7:00am 🕐"
            )
            return

        logger.info(f"🤖 SuperBot v5.0 — {ahora} | Bloque: {bloque}")
        if RAPIDAPI_KEY:
            logger.info("📡 RapidAPI H2H: ACTIVO")
        else:
            logger.warning("📡 RapidAPI H2H: inactivo (agrega RAPIDAPI_KEY)")

        # ── Obtener datos ─────────────────────────────────────
        fixtures_hoy       = get_fixtures_hoy_rapidapi()
        rapidapi_h2h_calls = 0

        todos = get_todos_los_partidos()
        if not todos:
            enviar_telegram(
                f"⚠️ <b>SuperBot v5.0 — sin datos</b>\n"
                f"📅 {safe_html(ahora)} (Colombia)\n\n"
                f"The Odds API no devolvió partidos."
            )
            return

        partidos_hoy = filtrar_partidos_hoy(todos)

        # ── Contadores ────────────────────────────────────────
        alertas_enviadas   = 0
        partidos_vistos    = 0
        partidos_filtrados = 0
        mejor_score        = 0
        mejor_partido      = ""
        ligas_vistas: set  = set()

        # Acumular alertas candidatas antes de enviar
        # para poder marcar el mejor score por franja horaria
        candidatas: list[dict] = []

        # ── Análisis partido a partido ────────────────────────
        for partido in partidos_hoy:
            local         = partido.get("home_team", "?")
            visitante     = partido.get("away_team", "?")
            hora_col      = hora_local_col(partido.get("commence_time", ""))
            sport_key     = partido.get("sport_key", "")
            liga          = nombre_liga(partido)
            commence_time = partido.get("commence_time", "")
            draw_rate     = get_draw_rate(sport_key)
            under25_rate  = get_under25_rate(sport_key)

            ligas_vistas.add(liga)
            partidos_vistos += 1

            # ── Extraer cuotas ────────────────────────────────
            cuota_loc, cuota_emp, cuota_vis, nbm_h2h = extraer_cuotas_h2h(partido)
            cuota_u25, cuota_o25, nbm_u25            = extraer_cuotas_under25(partido)

            if not cuota_emp:
                continue   # sin mercado h2h — no se puede analizar empate

            # ── Score empate ──────────────────────────────────
            if not (2.5 <= cuota_emp <= 4.2):
                continue   # fuera del rango de cuotas válido

            if nbm_h2h < MIN_BOOKMAKERS_DRAW:
                razones_draw  = []
                rechazos_draw = [f"pocos bookmakers ({nbm_h2h})"]
                score_draw    = 0
                prob_real_d   = prob_imp_d = 0
                partidos_filtrados += 1
            else:
                score_draw, razones_draw, rechazos_draw, prob_real_d, prob_imp_d = \
                    calcular_score_draw(draw_rate, cuota_loc, cuota_emp, cuota_vis, nbm_h2h)
                if rechazos_draw:
                    partidos_filtrados += 1

            # ── RapidAPI H2H — solo si pasa umbral previo ─────
            score_draw_pre = score_draw if not rechazos_draw else 0
            if (
                score_draw_pre > PRE_THRESHOLD
                and RAPIDAPI_KEY
                and rapidapi_h2h_calls < RAPIDAPI_MAX_H2H_POR_RUN
            ):
                fixture_ra = buscar_fixture_rapidapi(local, visitante, fixtures_hoy)
                if fixture_ra:
                    home_id = fixture_ra["teams"]["home"]["id"]
                    away_id = fixture_ra["teams"]["away"]["id"]
                    h2h     = get_h2h_rapidapi(home_id, away_id)
                    rapidapi_h2h_calls += 1
                    mod_draw, txt_h2h = calcular_modificadores_rapidapi(h2h or [])
                    if txt_h2h and not rechazos_draw:
                        razones_draw.append(txt_h2h)
                        score_draw = max(0, min(score_draw + mod_draw, 100))

            # ── Bonus Under 2.5 ───────────────────────────────
            under25_bonus = 0
            razon_u25     = None
            if cuota_u25 and not rechazos_draw:
                under25_bonus, razon_u25 = calcular_bonus_under25(
                    under25_rate, cuota_u25, score_draw
                )
                if under25_bonus > 0 and razon_u25:
                    razones_draw.append(razon_u25)
                    score_draw = min(score_draw + under25_bonus, 100)

            # ── Log y tracking del mejor partido ──────────────
            logger.info(
                f"{local} vs {visitante} ({liga}) | "
                f"Empate:{score_draw} | U25bonus:{under25_bonus} | "
                f"H2H_calls:{rapidapi_h2h_calls}"
            )

            score_efectivo = score_draw if not rechazos_draw else 0
            if score_efectivo > mejor_score:
                mejor_score   = score_efectivo
                mejor_partido = (
                    f"{local} vs {visitante} ({liga}) "
                    f"— Score:{score_draw}/100 a las {hora_col}"
                )

            # ── ¿Pasa el umbral mínimo? ────────────────────────
            draw_id  = build_alert_id("draw", local, visitante, sport_key, commence_time)
            draw_ok  = (
                score_draw >= SCORE_MIN_DRAW
                and not rechazos_draw
                and draw_id not in existing_ids
            )

            if draw_ok:
                candidatas.append({
                    "draw_id":      draw_id,
                    "score_draw":   score_draw,
                    "liga":         liga,
                    "local":        local,
                    "visitante":    visitante,
                    "cuota_emp":    cuota_emp,
                    "cuota_loc":    cuota_loc,
                    "cuota_vis":    cuota_vis,
                    "prob_real_d":  prob_real_d,
                    "prob_imp_d":   prob_imp_d,
                    "nbm_h2h":      nbm_h2h,
                    "hora_col":     hora_col,
                    "razones_draw": razones_draw,
                    "draw_rate":    draw_rate,
                    "under25_bonus": under25_bonus,
                    "cuota_u25":    cuota_u25,
                    "razon_u25":    razon_u25,
                    "sport_key":    sport_key,
                    "commence_time": commence_time,
                })

        # ── Determinar mejor score por franja horaria ─────────
        # Agrupa candidatas por hora_col; marca la de mejor score en cada franja
        mejores_por_franja: dict[str, str] = {}
        for c in candidatas:
            hf = c["hora_col"]
            if hf not in mejores_por_franja or c["score_draw"] > candidatas[
                next(i for i, x in enumerate(candidatas) if x["draw_id"] == mejores_por_franja[hf])
            ]["score_draw"]:
                mejores_por_franja[hf] = c["draw_id"]

        # ── Enviar alertas ────────────────────────────────────
        log_draws: list[tuple] = []

        for c in candidatas:
            es_mejor = (mejores_por_franja.get(c["hora_col"]) == c["draw_id"]
                        and len([x for x in candidatas if x["hora_col"] == c["hora_col"]]) > 1)

            msg = formatear_alerta_draw(
                liga           = c["liga"],
                local          = c["local"],
                visitante      = c["visitante"],
                score          = c["score_draw"],
                cuota_loc      = c["cuota_loc"],
                cuota_emp      = c["cuota_emp"],
                cuota_vis      = c["cuota_vis"],
                prob_real      = c["prob_real_d"],
                prob_imp       = c["prob_imp_d"],
                num_bm         = c["nbm_h2h"],
                hora_col       = c["hora_col"],
                razones        = c["razones_draw"],
                draw_rate      = c["draw_rate"],
                under25_bonus  = c["under25_bonus"],
                cuota_under25  = c["cuota_u25"],
                razon_under25  = c["razon_u25"],
                es_mejor_score = es_mejor,
            )

            if not msg:
                continue

            if enviar_telegram(msg):
                alertas_enviadas += 1
                log_draws.append((
                    c["local"], c["visitante"], c["liga"],
                    c["score_draw"], c["hora_col"], c["under25_bonus"],
                ))
                if registrar_alerta(
                    historial      = historial,
                    tipo           = "draw",
                    local          = c["local"],
                    visitante      = c["visitante"],
                    liga           = c["liga"],
                    score          = c["score_draw"],
                    cuota          = c["cuota_emp"],
                    hora_col       = c["hora_col"],
                    sport_key      = c["sport_key"],
                    commence_time  = c["commence_time"],
                    under25_bonus  = c["under25_bonus"],
                    cuota_under25  = c["cuota_u25"],
                ):
                    existing_ids.add(c["draw_id"])

                guardar_historial(historial)
                sincronizar_google_sheets(historial)
                time.sleep(1)

        # ── Mensaje de resumen ────────────────────────────────
        guardar_historial(historial)

        pendientes_count = historial["stats"]["pendientes"]
        stats     = calcular_stats_detalladas(historial)
        stats_txt = formatear_reporte_stats(stats, pendientes_count)

        if alertas_enviadas == 0:
            enviar_telegram(
                formatear_sin_alertas(
                    bloque             = bloque,
                    ahora              = ahora,
                    partidos_vistos    = partidos_vistos,
                    ligas_vistas       = ligas_vistas,
                    partidos_filtrados = partidos_filtrados,
                    mejor_partido      = mejor_partido,
                    score_minimo       = SCORE_MIN_DRAW,
                    rapidapi_h2h_calls = rapidapi_h2h_calls,
                    stats_txt          = stats_txt,
                )
            )
        else:
            enviar_telegram(
                formatear_resumen_alertas(
                    bloque             = bloque,
                    ahora              = ahora,
                    partidos_vistos    = partidos_vistos,
                    ligas_vistas       = ligas_vistas,
                    log_draws          = log_draws,
                    alertas_enviadas   = alertas_enviadas,
                    score_min_draw     = SCORE_MIN_DRAW,
                    rapidapi_h2h_calls = rapidapi_h2h_calls,
                    stats_txt          = stats_txt,
                )
            )

        logger.info(
            f"✅ Fin v5.0 — Empates:{alertas_enviadas} | "
            f"H2H:{rapidapi_h2h_calls} calls"
        )

        # ── Cierre del día (~10pm Colombia) ───────────────────
        if es_bloque_cierre():
            logger.info("🌙 Bloque de cierre — análisis Claude + backup GitHub...")
            analisis_diario_claude(historial)
            backup_historial_github()

    except Exception as e:
        logger.critical(f"❌ Error crítico: {type(e).__name__}: {e}")
        enviar_telegram(
            f"❌ <b>ERROR CRÍTICO SuperBot v5.0</b>\n"
            f"{type(e).__name__}: {str(e)[:150]}"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
