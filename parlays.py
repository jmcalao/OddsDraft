# ═══════════════════════════════════════════════════════════════
#  🤖 SUPERBOT v5.0 — parlays.py
#
#  Parlay semanal de 10-14 picks — sábado y domingo.
#  Stake fijo: $2.000 COP (boleto de lotería controlado).
#
#  LÓGICA DE SELECCIÓN:
#  1. Consulta The Odds API para TODOS los deportes disponibles
#  2. Por cada partido/evento, calcula EV de cada outcome
#  3. Selecciona los N picks con mayor EV positivo y cuota razonable
#  4. Filtra para no acumular el mismo deporte más de 4 veces
#  5. Calcula cuota combinada y ganancia potencial
#  6. Gemini escribe un comentario (opcional)
#  7. Envía alerta Telegram + registra en sheet
#
#  CUOTA OBJETIVO POR PICK: 1.70 – 2.80
#  (más altas reducen probabilidad real, más bajas no valen la pena)
#
#  CONSUMO API: ~1-2 requests por parlay → ~8-10/mes para 2/semana
# ═══════════════════════════════════════════════════════════════

import logging
import json
from datetime import datetime, timezone, timedelta

from config import API_KEY, ODDS_BASE, SESION, BANKROLL
from utils  import hora_colombia, safe_html

logger = logging.getLogger(__name__)

# ── Configuración del parlay ──────────────────────────────────
PARLAY_STAKE        = 2_000          # COP fijo, no cambia con martingala
PARLAY_MIN_PICKS    = 10
PARLAY_MAX_PICKS    = 14
PARLAY_MIN_CUOTA    = 1.65           # cuota mínima por pick
PARLAY_MAX_CUOTA    = 2.90           # cuota máxima por pick
PARLAY_MAX_POR_DEPORTE = 4           # máximo picks del mismo deporte

# Deportes a consultar — en orden de prioridad
# The Odds API los llama "sports keys"
DEPORTES_PARLAY = [
    # Fútbol (varias ligas — ya las conocemos bien)
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_germany_bundesliga",
    "soccer_italy_serie_a",
    "soccer_france_ligue_one",
    "soccer_brazil_campeonato",
    "soccer_argentina_primera_division",
    "soccer_colombia_primera_a",
    "soccer_mexico_ligamx",
    "soccer_usa_mls",
    # Tenis
    "tennis_atp_us_open",
    "tennis_wta_us_open",
    "tennis_atp_wimbledon",
    "tennis_wta_wimbledon",
    "tennis_atp_french_open",
    # Basketball
    "basketball_nba",
    "basketball_euroleague",
    # Béisbol
    "baseball_mlb",
    # Hockey
    "icehockey_nhl",
    # Rugby
    "rugbyleague_nrl",
    # Americano
    "americanfootball_nfl",
    # Cricket
    "cricket_icc_world_cup",
]

# Nombres legibles por deporte
DEPORTE_NOMBRE = {
    "soccer":           "⚽ Fútbol",
    "tennis":           "🎾 Tenis",
    "basketball":       "🏀 Basketball",
    "baseball":         "⚾ Béisbol",
    "icehockey":        "🏒 Hockey",
    "rugbyleague":      "🏉 Rugby",
    "americanfootball": "🏈 Fútbol Americano",
    "cricket":          "🏏 Cricket",
}


# ─── OBTENER DEPORTES DISPONIBLES ─────────────────────────────
def get_sports_disponibles() -> list[str]:
    """
    Consulta /sports para obtener los deportes activos ahora.
    Filtra solo los que están en temporada (in_season=true).
    Retorna lista de sport_keys disponibles.
    Costo: 1 request.
    """
    try:
        r = SESION.get(
            f"{ODDS_BASE}/sports",
            params={"apiKey": API_KEY},
            timeout=15,
        )
        if not r.ok:
            logger.warning(f"⚠️ get_sports: {r.status_code}")
            return DEPORTES_PARLAY[:8]   # fallback a los primeros 8

        sports = r.json()
        activos = [
            s["key"] for s in sports
            if s.get("active") and s.get("has_outrights") is False
            # has_outrights=False = es un mercado de partidos, no outright
        ]
        logger.info(f"📡 {len(activos)} deportes activos en The Odds API")
        return activos
    except Exception as e:
        logger.warning(f"⚠️ get_sports_disponibles: {e}")
        return DEPORTES_PARLAY[:8]


# ─── OBTENER ODDS PARA PARLAY ─────────────────────────────────
def get_odds_parlay(sport_keys: list[str]) -> list[dict]:
    """
    Obtiene partidos con cuotas h2h para una lista de deportes.
    Filtra solo eventos de las próximas 48 horas.
    Costo: 1 request por sport_key (usa batch cuando es posible).
    """
    todos = []
    ahora = datetime.now(timezone.utc)
    limite = ahora + timedelta(hours=48)

    # The Odds API permite pasar múltiples sports en una sola llamada
    # usando el endpoint /sports/upcoming/odds (si está disponible en el plan)
    # Fallback: iterar por deporte pero limitar a los más activos
    sports_a_consultar = [s for s in sport_keys if any(
        s.startswith(dep) for dep in [
            "soccer", "tennis", "basketball", "baseball",
            "icehockey", "rugbyleague", "americanfootball"
        ]
    )][:12]   # máximo 12 para no gastar demasiados requests

    for sport_key in sports_a_consultar:
        try:
            r = SESION.get(
                f"{ODDS_BASE}/sports/{sport_key}/odds",
                params={
                    "apiKey":     API_KEY,
                    "regions":    "eu",
                    "markets":    "h2h",
                    "oddsFormat": "decimal",
                    "dateFormat": "iso",
                },
                timeout=20,
            )
            if not r.ok:
                continue

            for evento in r.json():
                ct = evento.get("commence_time", "")
                if not ct:
                    continue
                try:
                    ct_dt = datetime.fromisoformat(ct.replace("Z", "+00:00"))
                    if ahora <= ct_dt <= limite:
                        evento["_sport_key"] = sport_key
                        todos.append(evento)
                except Exception:
                    pass

        except Exception as e:
            logger.warning(f"⚠️ odds {sport_key}: {e}")
            continue

    usados    = r.headers.get("x-requests-used",      "?") if todos else "?"
    restantes = r.headers.get("x-requests-remaining", "?") if todos else "?"
    logger.info(
        f"📡 Parlay fetch: {len(todos)} eventos | "
        f"API: {usados} usados / {restantes} restantes"
    )
    return todos


# ─── CALCULAR EV DE UN PICK ───────────────────────────────────
def _ev_pick(cuota: float, prob_base: float) -> float:
    """EV = prob_real × cuota - 1"""
    return prob_base * cuota - 1.0


def _prefijo_deporte(sport_key: str) -> str:
    for prefijo in DEPORTE_NOMBRE:
        if sport_key.startswith(prefijo):
            return prefijo
    return "otro"


# ─── SELECCIONAR PICKS ────────────────────────────────────────
def seleccionar_picks(eventos: list[dict]) -> list[dict]:
    """
    De todos los eventos, selecciona los mejores picks para el parlay.

    Criterios:
    - Cuota del pick en rango [PARLAY_MIN_CUOTA, PARLAY_MAX_CUOTA]
    - EV positivo (prob implícita < prob estimada)
    - No más de PARLAY_MAX_POR_DEPORTE picks del mismo deporte
    - Máximo un pick por evento (el de mejor EV)
    - Ordenados por EV descendente, tomar los top PARLAY_MAX_PICKS

    Para fútbol: favorito LOCAL (cuota más baja, más probable).
    Para otros deportes: el favorito absoluto del evento.
    """
    candidatos = []

    for evento in eventos:
        sport_key  = evento.get("_sport_key", "")
        home_team  = evento.get("home_team", "")
        away_team  = evento.get("away_team", "")
        ct         = evento.get("commence_time", "")
        liga       = evento.get("sport_title", sport_key)

        # Extraer cuotas h2h
        cuotas_por_equipo: dict[str, list[float]] = {}
        for bm in evento.get("bookmakers", []):
            for market in bm.get("markets", []):
                if market["key"] != "h2h":
                    continue
                for outcome in market["outcomes"]:
                    nombre = outcome["name"]
                    precio = float(outcome["price"])
                    cuotas_por_equipo.setdefault(nombre, []).append(precio)

        if not cuotas_por_equipo:
            continue

        # Promediar cuotas por outcome
        avg_cuotas = {
            nombre: round(sum(v) / len(v), 2)
            for nombre, v in cuotas_por_equipo.items()
        }

        # Calcular probabilidades implícitas
        suma_impl = sum(1.0 / c for c in avg_cuotas.values() if c > 0)

        # Hora Colombia
        try:
            utc = datetime.fromisoformat(ct.replace("Z", "+00:00"))
            hora_col = (utc - timedelta(hours=5)).strftime("%H:%M")
            fecha_col = (utc - timedelta(hours=5)).strftime("%Y-%m-%d")
        except Exception:
            hora_col = "??:??"
            fecha_col = hora_colombia().strftime("%Y-%m-%d")

        # Seleccionar el mejor pick del evento (favorito con EV más alto)
        mejor_pick = None
        mejor_ev   = -99

        for nombre, cuota in avg_cuotas.items():
            if not (PARLAY_MIN_CUOTA <= cuota <= PARLAY_MAX_CUOTA):
                continue
            # Probabilidad implícita normalizada
            prob_impl = (1.0 / cuota) / suma_impl if suma_impl > 0 else 0
            # Estimamos prob real con ajuste conservador (+5% al favorito)
            prob_real = prob_impl * 1.05
            ev        = _ev_pick(cuota, prob_real)
            if ev > mejor_ev:
                mejor_ev   = ev
                mejor_pick = {
                    "evento":     f"{home_team} vs {away_team}",
                    "pick":       nombre,
                    "cuota":      cuota,
                    "prob_impl":  round(prob_impl * 100, 1),
                    "ev":         round(ev * 100, 2),
                    "liga":       liga,
                    "sport_key":  sport_key,
                    "deporte":    _prefijo_deporte(sport_key),
                    "hora_col":   hora_col,
                    "fecha_col":  fecha_col,
                    "num_bm":     len(evento.get("bookmakers", [])),
                }

        if mejor_pick and mejor_ev > 0:
            candidatos.append(mejor_pick)

    # Ordenar por EV descendente
    candidatos.sort(key=lambda x: x["ev"], reverse=True)

    # Aplicar límite por deporte
    conteo_deporte: dict[str, int] = {}
    picks_finales  = []

    for c in candidatos:
        dep = c["deporte"]
        if conteo_deporte.get(dep, 0) >= PARLAY_MAX_POR_DEPORTE:
            continue
        conteo_deporte[dep] = conteo_deporte.get(dep, 0) + 1
        picks_finales.append(c)
        if len(picks_finales) >= PARLAY_MAX_PICKS:
            break

    # Si hay menos del mínimo, rellenar sin restricción de deporte
    if len(picks_finales) < PARLAY_MIN_PICKS:
        usados_ids = {p["evento"] for p in picks_finales}
        for c in candidatos:
            if c["evento"] not in usados_ids:
                picks_finales.append(c)
                usados_ids.add(c["evento"])
            if len(picks_finales) >= PARLAY_MIN_PICKS:
                break

    return picks_finales[:PARLAY_MAX_PICKS]


# ─── ANÁLISIS GEMINI ──────────────────────────────────────────
def _comentario_gemini_parlay(picks: list[dict], cuota_total: float) -> str:
    """Pide a Gemini un comentario corto sobre el parlay."""
    try:
        import os
        key = os.environ.get("GEMINI_API_KEY", "")
        if not key:
            return ""

        resumen = "\n".join(
            f"- {p['pick']} ({p['evento']}, {p['liga']}) cuota:{p['cuota']} EV:{p['ev']}%"
            for p in picks
        )
        prompt = (
            f"Eres analista de apuestas. Este es un parlay de {len(picks)} picks "
            f"con cuota combinada {cuota_total:.0f}x.\n\n{resumen}\n\n"
            f"Escribe UN párrafo de máximo 3 oraciones en español, sin markdown, "
            f"sin asteriscos. ¿Qué tan sólido se ve este boleto? "
            f"Menciona el pick más confiable y el más arriesgado."
        )
        r = SESION.post(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-2.0-flash:generateContent",
            params={"key": key},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": 150, "temperature": 0.4},
            },
            timeout=20,
        )
        if r.ok:
            texto = (
                r.json()
                .get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
                .strip()
            )
            return f"\n🤖 <i>{safe_html(texto)}</i>" if texto else ""
    except Exception as e:
        logger.warning(f"⚠️ Gemini parlay: {e}")
    return ""


# ─── FORMATEAR ALERTA PARLAY ──────────────────────────────────
def formatear_alerta_parlay(
    picks: list[dict],
    cuota_total: float,
    ganancia_pot: int,
    gemini_txt: str,
    fecha: str,
) -> str:
    """Formatea el mensaje completo del parlay para Telegram."""
    emoji_dep = {
        "soccer":           "⚽",
        "tennis":           "🎾",
        "basketball":       "🏀",
        "baseball":         "⚾",
        "icehockey":        "🏒",
        "rugbyleague":      "🏉",
        "americanfootball": "🏈",
        "cricket":          "🏏",
        "otro":             "🎮",
    }

    lineas_picks = []
    for i, p in enumerate(picks, 1):
        em = emoji_dep.get(p["deporte"], "🎮")
        lineas_picks.append(
            f"  {i:02d}. {em} <b>{safe_html(p['pick'])}</b> @ {p['cuota']}\n"
            f"      {safe_html(p['evento'])}\n"
            f"      {safe_html(p['liga'])} | {p['hora_col']} Col "
            f"| EV +{p['ev']}% | {p['prob_impl']}% impl."
        )

    picks_txt = "\n".join(lineas_picks)

    return (
        f"🎰 <b>PARLAY SEMANAL — {safe_html(fecha)}</b>\n\n"
        f"📋 <b>{len(picks)} picks seleccionados</b>\n\n"
        f"{picks_txt}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔢 Cuota combinada: <b>x{cuota_total:,.0f}</b>\n"
        f"💵 Apuesta: <b>${PARLAY_STAKE:,} COP</b>\n"
        f"🏆 Si ganas: <b>+${ganancia_pot:,} COP</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{gemini_txt}\n\n"
        f"⚠️ <i>Este es tu boleto de lotería semanal. "
        f"La probabilidad real es baja — es por diversión.</i>"
    )


# ─── GUARDAR EN HISTORIAL ─────────────────────────────────────
def registrar_parlay(historial: dict, picks: list[dict],
                     cuota_total: float, ganancia_pot: int,
                     fecha: str) -> str:
    """
    Guarda el parlay en historial['parlays'].
    Retorna el ID del parlay registrado.
    """
    import hashlib
    historial.setdefault("parlays", [])

    parlay_id = hashlib.sha1(
        f"{fecha}{''.join(p['evento'] for p in picks[:3])}".encode()
    ).hexdigest()[:12]

    # Evitar duplicados
    existentes = {p.get("id") for p in historial["parlays"]}
    if parlay_id in existentes:
        logger.info(f"↩️ Parlay duplicado omitido: {parlay_id}")
        return parlay_id

    historial["parlays"].append({
        "id":           parlay_id,
        "fecha":        fecha,
        "picks":        picks,
        "cuota_total":  cuota_total,
        "stake":        PARLAY_STAKE,
        "ganancia_pot": ganancia_pot,
        "estado":       "pendiente",   # → "ganado" / "perdido" (manual)
        "ganancia_real": 0,
    })
    logger.info(
        f"🎰 Parlay registrado: {len(picks)} picks | "
        f"cuota x{cuota_total:.0f} | pot. ${ganancia_pot:,}"
    )
    return parlay_id


# ─── FUNCIÓN PRINCIPAL ────────────────────────────────────────
def correr_parlay(historial: dict) -> bool:
    """
    Punto de entrada desde main.py.
    Solo corre sábado y domingo.
    Retorna True si se envió el parlay.
    """
    from telegram_bot import enviar_telegram
    from historial    import guardar_historial

    # Solo sábado (5) y domingo (6)
    dia_semana = hora_colombia().weekday()
    if dia_semana not in (5, 6):
        logger.info("🎰 Parlay: hoy no es sábado ni domingo — omitiendo")
        return False

    logger.info("🎰 Iniciando parlay semanal...")

    # 1. Obtener deportes activos
    sports_activos = get_sports_disponibles()

    # 2. Obtener odds
    eventos = get_odds_parlay(sports_activos)
    if not eventos:
        logger.warning("⚠️ Parlay: sin eventos disponibles")
        return False
    logger.info(f"🎰 {len(eventos)} eventos candidatos para el parlay")

    # 3. Seleccionar picks
    picks = seleccionar_picks(eventos)
    if len(picks) < PARLAY_MIN_PICKS:
        logger.warning(
            f"⚠️ Parlay: solo {len(picks)} picks disponibles "
            f"(mínimo {PARLAY_MIN_PICKS})"
        )
        if len(picks) < 5:
            return False   # muy pocos — no tiene sentido

    # 4. Calcular cuota combinada
    cuota_total  = round(
        __import__("math").prod(p["cuota"] for p in picks), 2
    )
    ganancia_pot = round(PARLAY_STAKE * cuota_total) - PARLAY_STAKE

    # 5. Análisis Gemini
    gemini_txt = _comentario_gemini_parlay(picks, cuota_total)

    # 6. Formatear y enviar
    fecha = hora_colombia().strftime("%Y-%m-%d")
    msg   = formatear_alerta_parlay(picks, cuota_total, ganancia_pot, gemini_txt, fecha)

    if not enviar_telegram(msg):
        logger.error("❌ Parlay: fallo al enviar Telegram")
        return False

    # 7. Registrar en historial
    registrar_parlay(historial, picks, cuota_total, ganancia_pot, fecha)
    guardar_historial(historial)

    logger.info(f"✅ Parlay enviado: x{cuota_total:.0f} | pot. ${ganancia_pot:,}")
    return True
