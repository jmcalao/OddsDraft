# ═══════════════════════════════════════════════════════════════
#  🤖 SUPERBOT v5.0 — fetcher.py
#  Comunicación con The Odds API:
#  - Obtener todos los partidos del día (h2h + totals)
#  - Extraer cuotas de empate (h2h)
#  - Extraer cuotas de Under 2.5 (totals) ← cambiado de 3.5
#  - Consultar scores finalizados para verificar resultados
# ═══════════════════════════════════════════════════════════════

import logging
from config import API_KEY, ODDS_BASE, SESION, DRAW_RATES, UNDER25_RATES
from config import DRAW_RATE_DEFAULT, UNDER25_RATE_DEFAULT
from utils  import es_hoy_y_futuro, hora_local_col

logger = logging.getLogger(__name__)


# ─── HELPERS DE RATES ─────────────────────────────────────────
def get_draw_rate(sport_key: str) -> float:
    return DRAW_RATES.get(sport_key, DRAW_RATE_DEFAULT)

def get_under25_rate(sport_key: str) -> float:
    return UNDER25_RATES.get(sport_key, UNDER25_RATE_DEFAULT)

def nombre_liga(partido: dict) -> str:
    return partido.get("sport_title") or partido.get("sport_key", "?")


# ─── ODDS API — PARTIDOS DEL DÍA ──────────────────────────────
def get_todos_los_partidos() -> list[dict]:
    """
    Obtiene todos los partidos de fútbol del día con cuotas h2h y totals.
    Una sola llamada cubre todos los deportes/ligas disponibles.
    Costo: 2 créditos por request.
    """
    try:
        r = SESION.get(
            f"{ODDS_BASE}/sports/soccer/odds",
            params={
                "apiKey":     API_KEY,
                "regions":    "eu",
                "markets":    "h2h,totals",
                "oddsFormat": "decimal",
                "dateFormat": "iso",
            },
            timeout=30,
        )
        usados    = r.headers.get("x-requests-used",      "?")
        restantes = r.headers.get("x-requests-remaining", "?")
        logger.info(f"📡 Odds API: {usados} usados / {restantes} restantes")

        if r.status_code == 401:
            logger.error("❌ API Key inválida"); return []
        if r.status_code == 429:
            logger.warning("⚠️ Rate limit Odds API"); return []
        if not r.ok:
            logger.warning(f"⚠️ Error Odds API {r.status_code}"); return []

        data = r.json()
        logger.info(f"✅ {len(data)} partidos recibidos")
        return data

    except Exception as e:
        logger.error(f"💥 get_todos_los_partidos: {type(e).__name__}: {e}")
        return []


# ─── ODDS API — SCORES FINALIZADOS ────────────────────────────
def get_scores_finalizados(sport_key: str) -> list[dict]:
    """
    Consulta resultados de partidos finalizados en los últimos 3 días.
    Endpoint gratuito — no consume créditos de cuota.
    """
    try:
        r = SESION.get(
            f"{ODDS_BASE}/sports/{sport_key}/scores",
            params={"apiKey": API_KEY, "daysFrom": 3},
            timeout=15,
        )
        if r.ok:
            return r.json()
    except Exception as e:
        logger.warning(f"⚠️ Scores {sport_key}: {type(e).__name__}")
    return []


# ─── EXTRACCIÓN DE CUOTAS H2H ─────────────────────────────────
def extraer_cuotas_h2h(
    partido: dict,
) -> tuple[float | None, float | None, float | None, int]:
    """
    Extrae cuotas promedio de local, empate y visitante del mercado h2h.
    Retorna: (cuota_loc, cuota_emp, cuota_vis, num_bookmakers)
    """
    try:
        home_team    = partido.get("home_team", "")
        cuotas_loc   = []
        cuotas_emp   = []
        cuotas_vis   = []

        for bm in partido.get("bookmakers", []):
            for market in bm.get("markets", []):
                if market["key"] != "h2h":
                    continue
                for outcome in market["outcomes"]:
                    price = float(outcome["price"])
                    if outcome["name"] == home_team:
                        cuotas_loc.append(price)
                    elif outcome["name"] == "Draw":
                        cuotas_emp.append(price)
                    else:
                        cuotas_vis.append(price)

        if not cuotas_emp:
            return None, None, None, 0

        avg = lambda lst: round(sum(lst) / len(lst), 2) if lst else None
        return avg(cuotas_loc), avg(cuotas_emp), avg(cuotas_vis), len(cuotas_emp)

    except Exception as e:
        logger.warning(f"⚠️ extraer_cuotas_h2h: {e}")
        return None, None, None, 0


# ─── EXTRACCIÓN DE CUOTAS UNDER 2.5 ──────────────────────────
def extraer_cuotas_under25(
    partido: dict,
) -> tuple[float | None, float | None, int]:
    """
    Extrae cuotas promedio de Under 2.5 y Over 2.5 del mercado totals.
    Retorna: (cuota_under25, cuota_over25, num_bookmakers)

    CAMBIO v5: busca point == 2.5 (antes era 3.5).
    """
    try:
        cuotas_under = []
        cuotas_over  = []

        for bm in partido.get("bookmakers", []):
            for market in bm.get("markets", []):
                if market["key"] != "totals":
                    continue
                for outcome in market["outcomes"]:
                    # Filtrar solo las líneas de 2.5
                    if abs(float(outcome.get("point", 0)) - 2.5) > 0.01:
                        continue
                    price = float(outcome["price"])
                    if outcome["name"] == "Under":
                        cuotas_under.append(price)
                    elif outcome["name"] == "Over":
                        cuotas_over.append(price)

        if not cuotas_under:
            return None, None, 0

        avg = lambda lst: round(sum(lst) / len(lst), 2) if lst else None
        return avg(cuotas_under), avg(cuotas_over), len(cuotas_under)

    except Exception as e:
        logger.warning(f"⚠️ extraer_cuotas_under25: {e}")
        return None, None, 0


# ─── FILTRO DE PARTIDOS ───────────────────────────────────────
def filtrar_partidos_hoy(todos: list[dict]) -> list[dict]:
    """
    Filtra la lista de partidos para quedarse solo con los que:
    - Son hoy en hora Colombia
    - Aún no han comenzado
    """
    return [
        p for p in todos
        if es_hoy_y_futuro(p.get("commence_time", ""))
    ]
