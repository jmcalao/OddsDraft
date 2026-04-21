# ═══════════════════════════════════════════════════════════════
#  🤖 SUPERBOT v5.0 — rapidapi.py
#  Integración con RapidAPI (api-football):
#  - Fixtures del día por fecha
#  - Head-to-Head de los últimos 10 partidos
#  - Modificadores de score basados en historial H2H
# ═══════════════════════════════════════════════════════════════

import logging
from difflib import SequenceMatcher

from config import RAPIDAPI_KEY, RAPIDAPI_BASE, SESION
from utils  import hora_colombia, _normalizar

logger = logging.getLogger(__name__)

_RAPIDAPI_HEADERS = {
    "X-RapidAPI-Key":  RAPIDAPI_KEY,
    "X-RapidAPI-Host": "api-football-v1.p.rapidapi.com",
}


# ─── FIXTURES DEL DÍA ─────────────────────────────────────────
def get_fixtures_hoy_rapidapi() -> list[dict]:
    """
    Obtiene todos los partidos de hoy desde RapidAPI (1 call).
    Retorna lista vacía si la key no está configurada.
    """
    if not RAPIDAPI_KEY:
        return []

    hoy = hora_colombia().date().isoformat()
    try:
        r = SESION.get(
            f"{RAPIDAPI_BASE}/fixtures",
            headers=_RAPIDAPI_HEADERS,
            params={"date": hoy, "timezone": "America/Bogota"},
            timeout=15,
        )
        if r.status_code == 429:
            logger.warning("⚠️ RapidAPI rate limit — fixtures")
            return []
        if r.ok:
            fixtures = r.json().get("response", [])
            logger.info(f"📡 RapidAPI: {len(fixtures)} fixtures hoy")
            return fixtures
        logger.warning(f"⚠️ RapidAPI fixtures: {r.status_code}")
    except Exception as e:
        logger.warning(f"⚠️ RapidAPI fixtures: {type(e).__name__}: {e}")

    return []


# ─── MATCH DE FIXTURE ─────────────────────────────────────────
def _match_score(a: str, b: str) -> float:
    """
    Similitud conservadora entre dos nombres de equipo normalizados.
    Evita cruces incorrectos entre equipos con nombres parecidos.
    """
    a = _normalizar(a or "")
    b = _normalizar(b or "")
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.92
    return SequenceMatcher(None, a, b).ratio()


def buscar_fixture_rapidapi(
    local: str,
    visitante: str,
    fixtures: list[dict],
) -> dict | None:
    """
    Busca el fixture de RapidAPI que corresponde al partido de The Odds API.
    Usa similitud de strings para tolerar diferencias en nombres.
    Umbral mínimo: 0.72 en ambos equipos para evitar falsos positivos.
    """
    if not fixtures:
        return None

    loc_n = _normalizar(local)
    vis_n = _normalizar(visitante)
    best  = None
    best_score = 0.0

    for f in fixtures:
        teams      = f.get("teams", {})
        home       = teams.get("home", {}) or {}
        away       = teams.get("away", {}) or {}
        home_name  = home.get("name", "")
        away_name  = away.get("name", "")
        home_short = home.get("shortName", "")
        away_short = away.get("shortName", "")

        hs = max(_match_score(loc_n, home_name), _match_score(loc_n, home_short))
        as_ = max(_match_score(vis_n, away_name), _match_score(vis_n, away_short))
        total = (hs + as_) / 2

        if hs >= 0.72 and as_ >= 0.72 and total > best_score:
            best       = f
            best_score = total

    return best


# ─── HEAD-TO-HEAD ─────────────────────────────────────────────
def get_h2h_rapidapi(home_id: int, away_id: int) -> list[dict] | None:
    """
    Obtiene los últimos 10 enfrentamientos H2H entre los dos equipos.
    Retorna None si falla o rate-limited.
    """
    if not RAPIDAPI_KEY:
        return None

    try:
        r = SESION.get(
            f"{RAPIDAPI_BASE}/fixtures/headtohead",
            headers=_RAPIDAPI_HEADERS,
            params={"h2h": f"{home_id}-{away_id}", "last": 10},
            timeout=10,
        )
        if r.status_code == 429:
            logger.warning("⚠️ RapidAPI rate limit — H2H")
            return None
        if r.ok:
            return r.json().get("response", [])
    except Exception as e:
        logger.warning(f"⚠️ get_h2h_rapidapi: {type(e).__name__}: {e}")

    return None


# ─── MODIFICADORES DE SCORE ───────────────────────────────────
def calcular_modificadores_rapidapi(
    h2h_matches: list[dict],
) -> tuple[int, str | None]:
    """
    Analiza el historial H2H y retorna un modificador para el score_draw.

    En v5 solo hay score_draw (no hay score_under independiente),
    por lo que se retorna un solo modificador entero + razón de texto.

    Retorna: (mod_draw, texto_razon)
    """
    if not h2h_matches:
        return 0, None

    try:
        terminados = [
            m for m in h2h_matches
            if m.get("fixture", {}).get("status", {}).get("short") in ("FT", "AET", "PEN")
        ]
        if not terminados:
            return 0, None

        total    = len(terminados)
        empates  = 0
        goles_lista = []

        for m in terminados:
            gh = m.get("goals", {}).get("home", 0) or 0
            ga = m.get("goals", {}).get("away", 0) or 0
            goles_lista.append(gh + ga)
            if gh == ga:
                empates += 1

        dr    = empates / total
        avg_g = round(sum(goles_lista) / total, 1)

        # Modificador por tasa de empates H2H
        if   dr >= 0.40: dm, dl = +8, "excelente"
        elif dr >= 0.30: dm, dl = +5, "bueno"
        elif dr >= 0.20: dm, dl = +2, "regular"
        elif dr >= 0.10: dm, dl = -3, "bajo"
        else:            dm, dl = -8, "muy bajo"

        signo = "+" if dm >= 0 else ""
        texto = (
            f"H2H empates {dl} — {empates}/{total} ({round(dr*100)}%) "
            f"| avg {avg_g} goles/partido: {signo}{dm}pts"
        )
        return dm, texto

    except Exception as e:
        logger.warning(f"⚠️ calcular_modificadores_rapidapi: {e}")
        return 0, None
