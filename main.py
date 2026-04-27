# ═══════════════════════════════════════════════════════════════
#  🤖 SUPERBOT v5.0 — main.py
#
#  CAMBIOS vs v4.6:
#  ✅ Código modular (config, utils, scoring, fetcher, etc.)
#  ✅ Under 3.5 → Under 2.5 integrado como BONUS al score_draw
#  ✅ Un solo tipo de alerta: EMPATE
#  ✅ Emoji ⭐ al mejor score cuando hay varios en la misma franja
#  ✅ Martingala x6 (factor 1.5): stake auto-ajustado por nivel
#  ✅ Alerta STOP al perder 6 consecutivas, reset automático al ganar
#  ✅ Bugs corregidos: funciones duplicadas, typo "Draft", SCORE_MINIMO doble
# ═══════════════════════════════════════════════════════════════

import sys
import time
import logging

from config import (
    validar_configuracion, SCORE_MIN_DRAW, MIN_BOOKMAKERS_DRAW,
    PRE_THRESHOLD, RAPIDAPI_MAX_H2H_POR_RUN, RAPIDAPI_KEY, BANKROLL,
)
from utils import (
    hora_colombia, get_bloque_actual, es_bloque_cierre,
    build_alert_id, safe_html,
)
from fetcher import (
    get_todos_los_partidos, filtrar_partidos_hoy,
    extraer_cuotas_h2h, extraer_cuotas_under25,
    get_draw_rate, get_under25_rate, nombre_liga,
)
from rapidapi import (
    get_fixtures_hoy_rapidapi, buscar_fixture_rapidapi,
    get_h2h_rapidapi, calcular_modificadores_rapidapi,
)
from scoring import calcular_score_draw, calcular_bonus_under25
from historial import (
    cargar_historial, guardar_historial, registrar_alerta,
    actualizar_resultados, actualizar_martingala,
    get_estado_martingala, necesita_stop_alert, marcar_stop_notificado,
    calcular_stats_detalladas, formatear_reporte_stats,
)
from sheets import sincronizar_google_sheets
from telegram_bot import (
    enviar_telegram, formatear_alerta_draw,
    formatear_sin_alertas, formatear_resumen_alertas,
    formatear_stop_martingala,
)
from gemini_ai import analisis_diario_gemini, analizar_partido_gemini
from backup   import backup_historial_github
from utils    import hora_local_col

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def main():
    try:
        validar_configuracion()
        bloque = get_bloque_actual()
        ahora  = hora_colombia().strftime("%Y-%m-%d %H:%M")

        # ── Cargar historial ──────────────────────────────────
        historial    = cargar_historial()
        existing_ids = {a.get("id") for a in historial.get("alertas", [])}

        # ── Actualizar resultados pendientes ──────────────────
        actualizadas = actualizar_resultados(historial)
        if actualizadas > 0:
            guardar_historial(historial)
            logger.info(f"📊 {actualizadas} alerta(s) actualizadas con resultado")
            sincronizar_google_sheets(historial)

        # ── Actualizar estado de Martingala ───────────────────
        mart_cambio = actualizar_martingala(historial)
        if mart_cambio:
            guardar_historial(historial)

        # ── Enviar STOP si corresponde (1 sola vez) ───────────
        if necesita_stop_alert(historial):
            balance = BANKROLL + sum(
                a.get("ganancia_real", 0)
                for a in historial["alertas"]
                if a.get("estado") != "pendiente"
            )
            enviar_telegram(formatear_stop_martingala(balance))
            marcar_stop_notificado(historial)
            guardar_historial(historial)

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

        # ── Estado actual de Martingala ───────────────────────
        mart = get_estado_martingala(historial)
        nivel_mart = mart["nivel"]
        mart_activa = mart["activa"]
        logger.info(
            f"🎯 Martingala: nivel {nivel_mart}/{6} | "
            f"racha_perdidas:{mart['racha_perdidas']} | "
            f"activa:{mart_activa}"
        )

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
        candidatas: list   = []

        # ── Análisis ──────────────────────────────────────────
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

            cuota_loc, cuota_emp, cuota_vis, nbm_h2h = extraer_cuotas_h2h(partido)
            cuota_u25, cuota_o25, nbm_u25            = extraer_cuotas_under25(partido)

            if not cuota_emp:
                continue
            if not (2.5 <= cuota_emp <= 4.2):
                continue

            # ── Score empate ──────────────────────────────────
            if nbm_h2h < MIN_BOOKMAKERS_DRAW:
                score_draw, razones_draw, rechazos_draw = 0, [], [f"pocos bookmakers ({nbm_h2h})"]
                prob_real_d = prob_imp_d = 0
                partidos_filtrados += 1
            else:
                score_draw, razones_draw, rechazos_draw, prob_real_d, prob_imp_d = \
                    calcular_score_draw(draw_rate, cuota_loc, cuota_emp, cuota_vis, nbm_h2h)
                if rechazos_draw:
                    partidos_filtrados += 1

            # ── RapidAPI H2H ──────────────────────────────────
            score_draw_pre = score_draw if not rechazos_draw else 0
            if (
                score_draw_pre > PRE_THRESHOLD
                and RAPIDAPI_KEY
                and rapidapi_h2h_calls < RAPIDAPI_MAX_H2H_POR_RUN
            ):
                fx = buscar_fixture_rapidapi(local, visitante, fixtures_hoy)
                if fx:
                    hid = fx["teams"]["home"]["id"]
                    aid = fx["teams"]["away"]["id"]
                    h2h = get_h2h_rapidapi(hid, aid)
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

            logger.info(
                f"{local} vs {visitante} ({liga}) | "
                f"Score:{score_draw} | U25bonus:{under25_bonus} | "
                f"H2H:{rapidapi_h2h_calls}"
            )

            # Mejor partido del run
            score_ef = score_draw if not rechazos_draw else 0
            if score_ef > mejor_score:
                mejor_score   = score_ef
                mejor_partido = (
                    f"{local} vs {visitante} ({liga}) "
                    f"— Score:{score_draw}/100 a las {hora_col}"
                )

            # ── ¿Pasa el umbral? ──────────────────────────────
            draw_id = build_alert_id("draw", local, visitante, sport_key, commence_time)
            if (
                score_draw >= SCORE_MIN_DRAW
                and not rechazos_draw
                and draw_id not in existing_ids
            ):
                candidatas.append({
                    "draw_id":       draw_id,
                    "score_draw":    score_draw,
                    "liga":          liga,
                    "local":         local,
                    "visitante":     visitante,
                    "cuota_emp":     cuota_emp,
                    "cuota_loc":     cuota_loc,
                    "cuota_vis":     cuota_vis,
                    "prob_real_d":   prob_real_d,
                    "prob_imp_d":    prob_imp_d,
                    "nbm_h2h":       nbm_h2h,
                    "hora_col":      hora_col,
                    "razones_draw":  razones_draw,
                    "draw_rate":     draw_rate,
                    "under25_bonus": under25_bonus,
                    "cuota_u25":     cuota_u25,
                    "razon_u25":     razon_u25,
                    "sport_key":     sport_key,
                    "commence_time": commence_time,
                })

        # ── Mejor score por franja horaria ────────────────────
        # Para cada hora_col que tenga MÁS de una candidata,
        # marca la de mayor score con ⭐
        mejores_por_franja: dict[str, str] = {}
        franja_count: dict[str, int]       = {}

        for c in candidatas:
            hf = c["hora_col"]
            franja_count[hf] = franja_count.get(hf, 0) + 1
            if hf not in mejores_por_franja:
                mejores_por_franja[hf] = c["draw_id"]
            else:
                # Comparar con la actual mejor
                actual_mejor_id = mejores_por_franja[hf]
                actual_mejor = next(
                    (x for x in candidatas if x["draw_id"] == actual_mejor_id), None
                )
                if actual_mejor and c["score_draw"] > actual_mejor["score_draw"]:
                    mejores_por_franja[hf] = c["draw_id"]

        # ── Enviar alertas ────────────────────────────────────
        log_draws: list = []

        for c in candidatas:
            hf       = c["hora_col"]
            es_mejor = (
                franja_count.get(hf, 1) > 1
                and mejores_por_franja.get(hf) == c["draw_id"]
            )

            msg = formatear_alerta_draw(
                liga              = c["liga"],
                local             = c["local"],
                visitante         = c["visitante"],
                score             = c["score_draw"],
                cuota_loc         = c["cuota_loc"],
                cuota_emp         = c["cuota_emp"],
                cuota_vis         = c["cuota_vis"],
                prob_real         = c["prob_real_d"],
                prob_imp          = c["prob_imp_d"],
                num_bm            = c["nbm_h2h"],
                hora_col          = c["hora_col"],
                razones           = c["razones_draw"],
                draw_rate         = c["draw_rate"],
                nivel_martingala  = nivel_mart,
                martingala_activa = mart_activa,
                under25_bonus     = c["under25_bonus"],
                cuota_under25     = c["cuota_u25"],
                es_mejor_score    = es_mejor,
            )

            if not msg:
                continue

            #--Análisis Gemini por partido (opcional) ────────
            gemini_txt = analizar_partido_gemini(
                local         = c["local"],
                visitante     = c["visitante"],
                liga          = c["liga"],
                score         = c["score_draw"],
                cuota_emp     = c["cuota_emp"],
                cuota_loc     = c["cuota_loc"],
                cuota_vis     = c["cuota_vis"],
                draw_rate     = c["draw_rate"],
                under25_bonus = c["under25_bonus"],
                razones       = c["razones_draw"],
            )
            if  gemini_txt:
                msg = msg + gemini_txt
                
            if enviar_telegram(msg):
                alertas_enviadas += 1
                log_draws.append((
                    c["local"], c["visitante"], c["liga"],
                    c["score_draw"], c["hora_col"],
                    c["under25_bonus"], nivel_mart,
                ))
                if registrar_alerta(
                    historial        = historial,
                    tipo             = "draw",
                    local            = c["local"],
                    visitante        = c["visitante"],
                    liga             = c["liga"],
                    score            = c["score_draw"],
                    cuota            = c["cuota_emp"],
                    hora_col         = c["hora_col"],
                    sport_key        = c["sport_key"],
                    commence_time    = c["commence_time"],
                    nivel_martingala = nivel_mart,
                    under25_bonus    = c["under25_bonus"],
                    cuota_under25    = c["cuota_u25"],
                ):
                    existing_ids.add(c["draw_id"])

                guardar_historial(historial)
                sincronizar_google_sheets(historial)
                time.sleep(1)

        # ── Resumen ───────────────────────────────────────────
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
            f"H2H:{rapidapi_h2h_calls} calls | Mart nivel:{nivel_mart}"
        )

        # ── Cierre del día ────────────────────────────────────
        if es_bloque_cierre():
            logger.info("🌙 Bloque cierre — análisis Gemini + backup GitHub...")
            analisis_diario_gemini(historial)
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
