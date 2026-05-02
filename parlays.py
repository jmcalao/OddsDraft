# ═══════════════════════════════════════════════════════════════
#  🤖 SUPERBOT v5.0 — parlays.py  (v2)
#
#  CAMBIOS v2:
#  ✅ Fix: fallback de relleno también respeta límite por deporte
#  ✅ Preferencia por equipo LOCAL (bonus EV +2%)
#  ✅ Gemini con Google Search — busca noticias reales de cada pick
#  ✅ Mensaje incluye contexto por pick (bajas, forma, playoff...)
# ═══════════════════════════════════════════════════════════════

import logging
import math
from datetime import datetime, timezone, timedelta

from config import API_KEY, ODDS_BASE, SESION, BANKROLL
from utils  import hora_colombia, safe_html

logger = logging.getLogger(__name__)

PARLAY_STAKE           = 2_000
PARLAY_MIN_PICKS       = 10
PARLAY_MAX_PICKS       = 14
PARLAY_MIN_CUOTA       = 1.65
PARLAY_MAX_CUOTA       = 2.90
PARLAY_MAX_POR_DEPORTE = 4      # máximo picks del mismo deporte — SIEMPRE se respeta
LOCAL_EV_BONUS         = 0.02   # bonus EV para equipos locales

DEPORTES_PARLAY = [
    "soccer_epl", "soccer_spain_la_liga", "soccer_germany_bundesliga",
    "soccer_italy_serie_a", "soccer_france_ligue_one",
    "soccer_brazil_campeonato", "soccer_argentina_primera_division",
    "soccer_colombia_primera_a", "soccer_mexico_ligamx", "soccer_usa_mls",
    "tennis_atp_us_open", "tennis_wta_us_open",
    "tennis_atp_wimbledon", "tennis_wta_wimbledon",
    "basketball_nba", "basketball_euroleague",
    "baseball_mlb", "icehockey_nhl",
    "rugbyleague_nrl", "americanfootball_nfl",
    "cricket_icc_world_cup",
]

DEPORTE_EMOJI = {
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


# ─── HELPERS ──────────────────────────────────────────────────
def _prefijo_deporte(sport_key: str) -> str:
    for p in DEPORTE_EMOJI:
        if sport_key.startswith(p):
            return p
    return "otro"


def get_sports_disponibles() -> list[str]:
    try:
        r = SESION.get(
            f"{ODDS_BASE}/sports",
            params={"apiKey": API_KEY},
            timeout=15,
        )
        if r.ok:
            activos = [
                s["key"] for s in r.json()
                if s.get("active") and not s.get("has_outrights", True)
            ]
            logger.info(f"📡 {len(activos)} deportes activos")
            return activos
    except Exception as e:
        logger.warning(f"⚠️ get_sports: {e}")
    return DEPORTES_PARLAY[:10]


def get_odds_parlay(sport_keys: list[str]) -> list[dict]:
    todos = []
    ahora  = datetime.now(timezone.utc)
    limite = ahora + timedelta(hours=48)

    priority = ["soccer", "basketball", "icehockey", "americanfootball",
                "baseball", "tennis", "rugbyleague"]
    sports_sorted = sorted(
        [s for s in sport_keys if any(s.startswith(p) for p in priority)],
        key=lambda x: next((i for i, p in enumerate(priority) if x.startswith(p)), 99)
    )[:14]

    last_r = None
    for sport_key in sports_sorted:
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
            last_r = r
            if not r.ok:
                continue
            for ev in r.json():
                ct = ev.get("commence_time", "")
                try:
                    ct_dt = datetime.fromisoformat(ct.replace("Z", "+00:00"))
                    if ahora <= ct_dt <= limite:
                        ev["_sport_key"] = sport_key
                        todos.append(ev)
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"⚠️ odds {sport_key}: {e}")

    if last_r:
        logger.info(
            f"📡 Parlay: {len(todos)} eventos | "
            f"API usados:{last_r.headers.get('x-requests-used','?')} "
            f"restantes:{last_r.headers.get('x-requests-remaining','?')}"
        )
    return todos


# ─── SELECCIONAR PICKS ────────────────────────────────────────
def seleccionar_picks(eventos: list[dict]) -> list[dict]:
    """
    Selecciona top picks con EV positivo.
    REGLAS:
    - Cuota pick en [PARLAY_MIN_CUOTA, PARLAY_MAX_CUOTA]
    - Preferencia por equipo LOCAL (bonus +2% EV)
    - Máximo PARLAY_MAX_POR_DEPORTE por deporte — SIEMPRE, incluso en fallback
    - Máximo 1 pick por evento
    - Ordenados por EV desc → top PARLAY_MAX_PICKS
    """
    candidatos = []
    ahora = datetime.now(timezone.utc)

    for evento in eventos:
        sport_key = evento.get("_sport_key", "")
        home_team = evento.get("home_team", "")
        away_team = evento.get("away_team", "")
        ct        = evento.get("commence_time", "")
        liga      = evento.get("sport_title", sport_key)
        deporte   = _prefijo_deporte(sport_key)

        # Extraer cuotas promedio por outcome
        cuotas_map: dict[str, list[float]] = {}
        for bm in evento.get("bookmakers", []):
            for mkt in bm.get("markets", []):
                if mkt["key"] != "h2h":
                    continue
                for out in mkt["outcomes"]:
                    cuotas_map.setdefault(out["name"], []).append(float(out["price"]))

        if not cuotas_map:
            continue

        avg_cuotas = {
            name: round(sum(v)/len(v), 2)
            for name, v in cuotas_map.items()
        }

        suma_impl = sum(1.0/c for c in avg_cuotas.values() if c > 0)

        try:
            utc      = datetime.fromisoformat(ct.replace("Z", "+00:00"))
            hora_col = (utc - timedelta(hours=5)).strftime("%H:%M")
            fecha_col = (utc - timedelta(hours=5)).strftime("%Y-%m-%d")
        except Exception:
            hora_col = "??:??"
            fecha_col = hora_colombia().strftime("%Y-%m-%d")

        # Evaluar cada outcome — con bonus para el local
        mejor_pick = None
        mejor_ev   = -99.0

        for nombre, cuota in avg_cuotas.items():
            if not (PARLAY_MIN_CUOTA <= cuota <= PARLAY_MAX_CUOTA):
                continue

            prob_impl = (1.0/cuota) / suma_impl if suma_impl > 0 else 0
            prob_real = prob_impl * 1.05

            # Bonus local
            es_local = (nombre == home_team)
            ev = prob_real * cuota - 1.0
            if es_local:
                ev += LOCAL_EV_BONUS

            if ev > mejor_ev:
                mejor_ev   = ev
                mejor_pick = {
                    "evento":    f"{home_team} vs {away_team}",
                    "pick":      nombre,
                    "es_local":  es_local,
                    "cuota":     cuota,
                    "prob_impl": round(prob_impl * 100, 1),
                    "ev":        round(ev * 100, 2),
                    "liga":      liga,
                    "sport_key": sport_key,
                    "deporte":   deporte,
                    "hora_col":  hora_col,
                    "fecha_col": fecha_col,
                    "num_bm":    len(evento.get("bookmakers", [])),
                    "home_team": home_team,
                    "away_team": away_team,
                }

        if mejor_pick and mejor_ev > 0:
            candidatos.append(mejor_pick)

    # Ordenar por EV descendente
    candidatos.sort(key=lambda x: x["ev"], reverse=True)

    # Aplicar límite por deporte — sin excepciones, sin fallback relajado
    conteo: dict[str, int] = {}
    picks_finales: list[dict] = []

    for c in candidatos:
        dep = c["deporte"]
        if conteo.get(dep, 0) >= PARLAY_MAX_POR_DEPORTE:
            continue
        conteo[dep] = conteo.get(dep, 0) + 1
        picks_finales.append(c)
        if len(picks_finales) >= PARLAY_MAX_PICKS:
            break

    local_count = sum(1 for p in picks_finales if p.get("es_local"))
    logger.info(
        f"🎰 Picks seleccionados: {len(picks_finales)} | "
        f"Locales: {local_count} | "
        f"Deportes: {dict(conteo)}"
    )
    return picks_finales


# ─── GEMINI CON GOOGLE SEARCH ─────────────────────────────────
def _contexto_gemini_picks(picks: list[dict]) -> dict[str, str]:
    """
    Usa Gemini 2.0 Flash con Google Search para buscar contexto
    real de cada pick: bajas, forma reciente, playoff, ventaja local, etc.

    Retorna dict: evento → texto de contexto (1 oración)
    Si Gemini no está disponible retorna dict vacío.
    """
    import os
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return {}

    # Un solo prompt con todos los partidos — eficiente en requests
    partidos_txt = "\n".join(
        f"- {p['pick']} ({p['evento']}, {p['liga']}, {p['fecha_col']})"
        for p in picks
    )

    prompt = (
        f"Busca información actual sobre estos partidos deportivos "
        f"y para CADA UNO escribe UNA sola oración en español "
        f"(máximo 15 palabras) explicando por qué el equipo indicado "
        f"tiene ventaja: bajas del rival, forma reciente, ventaja de local, "
        f"necesidad de puntos, playoff, etc. "
        f"Sé específico y usa datos reales.\n\n"
        f"Formato de respuesta — solo esto, sin markdown:\n"
        f"EVENTO: razon\n\n"
        f"Partidos:\n{partidos_txt}"
    )

    try:
        r = SESION.post(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-2.0-flash:generateContent",
            params={"key": key},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "tools": [{"google_search": {}}],
                "generationConfig": {
                    "maxOutputTokens": 600,
                    "temperature": 0.3,
                },
            },
            timeout=30,
        )
        if not r.ok:
            logger.warning(f"⚠️ Gemini search {r.status_code}: {r.text[:100]}")
            return {}

        texto = (
            r.json()
            .get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
            .strip()
        )

        # Parsear "EVENTO: razon" línea por línea
        contextos: dict[str, str] = {}
        for linea in texto.splitlines():
            if ":" in linea:
                partes = linea.split(":", 1)
                evento_key = partes[0].strip()
                razon      = partes[1].strip()
                # Buscar el pick que más se aproxima al evento_key
                for p in picks:
                    if (p["pick"].lower() in evento_key.lower()
                            or any(
                                word in evento_key.lower()
                                for word in p["pick"].lower().split()
                                if len(word) > 3
                            )):
                        contextos[p["evento"]] = razon
                        break
        return contextos

    except Exception as e:
        logger.warning(f"⚠️ Gemini search parlay: {e}")
        return {}


def _resumen_gemini_parlay(picks: list[dict], cuota_total: float) -> str:
    """Resumen general del parlay (sin search — rápido)."""
    import os
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return ""

    locales = sum(1 for p in picks if p.get("es_local"))
    deportes_usados = list({p["deporte"] for p in picks})

    prompt = (
        f"Parlay de {len(picks)} picks, cuota combinada x{cuota_total:,.0f}. "
        f"{locales} de {len(picks)} son equipos locales. "
        f"Deportes: {', '.join(deportes_usados)}. "
        f"Escribe 2 oraciones en español sin markdown. "
        f"¿Qué tan equilibrado está el boleto y cuál es la fortaleza principal?"
    )
    try:
        r = SESION.post(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-2.0-flash:generateContent",
            params={"key": key},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": 100, "temperature": 0.4},
            },
            timeout=15,
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
    except Exception as e:
        logger.warning(f"⚠️ Gemini resumen: {e}")
    return ""


# ─── FORMATEAR ALERTA ─────────────────────────────────────────
def formatear_alerta_parlay(
    picks: list[dict],
    cuota_total: float,
    ganancia_pot: int,
    contextos: dict[str, str],
    resumen: str,
    fecha: str,
) -> str:
    lineas = []
    local_tag  = "🏠"
    visita_tag = "✈️"

    for i, p in enumerate(picks, 1):
        em    = DEPORTE_EMOJI.get(p["deporte"], "🎮")
        loc   = local_tag if p.get("es_local") else visita_tag
        ctx   = contextos.get(p["evento"], "")
        ctx_l = f"\n      💬 <i>{safe_html(ctx)}</i>" if ctx else ""

        lineas.append(
            f"  {i:02d}. {em}{loc} <b>{safe_html(p['pick'])}</b> @ {p['cuota']}\n"
            f"      {safe_html(p['evento'])}\n"
            f"      {safe_html(p['liga'])} | {p['hora_col']} Col "
            f"| EV +{p['ev']}%{ctx_l}"
        )

    picks_txt = "\n".join(lineas)
    locales   = sum(1 for p in picks if p.get("es_local"))
    deportes  = len({p["deporte"] for p in picks})

    resumen_txt = f"\n🤖 <i>{safe_html(resumen)}</i>\n" if resumen else ""

    return (
        f"🎰 <b>PARLAY SEMANAL — {safe_html(fecha)}</b>\n\n"
        f"📋 <b>{len(picks)} picks</b> | "
        f"🏠 {locales} locales | "
        f"🎮 {deportes} deportes\n\n"
        f"{picks_txt}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔢 Cuota combinada: <b>x{cuota_total:,.0f}</b>\n"
        f"💵 Apuesta: <b>${PARLAY_STAKE:,} COP</b>\n"
        f"🏆 Si ganas: <b>+${ganancia_pot:,} COP</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{resumen_txt}\n"
        f"🏠 = local  ✈️ = visitante\n"
        f"⚠️ <i>Boleto de lotería semanal — por diversión.</i>"
    )


# ─── GUARDAR EN HISTORIAL ─────────────────────────────────────
def registrar_parlay(
    historial: dict,
    picks: list[dict],
    cuota_total: float,
    ganancia_pot: int,
    fecha: str,
) -> str:
    import hashlib
    historial.setdefault("parlays", [])

    parlay_id = hashlib.sha1(
        f"{fecha}{''.join(p['evento'] for p in picks[:3])}".encode()
    ).hexdigest()[:12]

    if parlay_id in {p.get("id") for p in historial["parlays"]}:
        logger.info(f"↩️ Parlay duplicado: {parlay_id}")
        return parlay_id

    historial["parlays"].append({
        "id":           parlay_id,
        "fecha":        fecha,
        "picks":        picks,
        "cuota_total":  cuota_total,
        "stake":        PARLAY_STAKE,
        "ganancia_pot": ganancia_pot,
        "estado":       "pendiente",
        "ganancia_real": 0,
    })
    logger.info(
        f"🎰 Parlay registrado: {parlay_id} | "
        f"{len(picks)} picks | x{cuota_total:.0f} | "
        f"pot. ${ganancia_pot:,}"
    )
    return parlay_id


# ─── PUNTO DE ENTRADA ─────────────────────────────────────────
def correr_parlay(historial: dict) -> bool:
    """Corre solo sábado y domingo. Retorna True si envió el parlay."""
    from telegram_bot import enviar_telegram
    from historial    import guardar_historial

    dia = hora_colombia().weekday()
    if dia not in (5, 6):
        logger.info("🎰 Parlay: no es sábado ni domingo")
        return False

    fecha = hora_colombia().strftime("%Y-%m-%d")

    # Evitar duplicado del mismo día
    if any(p.get("fecha") == fecha for p in historial.get("parlays", [])):
        logger.info(f"🎰 Parlay del {fecha} ya enviado hoy")
        return False

    logger.info("🎰 Iniciando parlay semanal...")

    sports   = get_sports_disponibles()
    eventos  = get_odds_parlay(sports)
    if not eventos:
        logger.warning("⚠️ Parlay: sin eventos")
        return False

    picks = seleccionar_picks(eventos)
    if len(picks) < 5:
        logger.warning(f"⚠️ Parlay: solo {len(picks)} picks válidos")
        return False

    cuota_total  = round(math.prod(p["cuota"] for p in picks), 2)
    ganancia_pot = round(PARLAY_STAKE * cuota_total) - PARLAY_STAKE

    # Gemini: contexto por pick (con Google Search) + resumen
    logger.info("🤖 Consultando Gemini con Google Search...")
    contextos = _contexto_gemini_picks(picks)
    resumen   = _resumen_gemini_parlay(picks, cuota_total)

    msg = formatear_alerta_parlay(
        picks, cuota_total, ganancia_pot, contextos, resumen, fecha
    )

    if not enviar_telegram(msg):
        logger.error("❌ Parlay: fallo Telegram")
        return False

    registrar_parlay(historial, picks, cuota_total, ganancia_pot, fecha)
    guardar_historial(historial)
    logger.info(f"✅ Parlay enviado: x{cuota_total:.0f} | ${ganancia_pot:,}")
    return True
