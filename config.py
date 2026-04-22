# ═══════════════════════════════════════════════════════════════
#  🤖 SUPERBOT v5.0 — config.py
#  Todas las variables de entorno, constantes y tablas de datos.
#
#  FIX v5.0.1: _env_int/_env_float con try/except — ya no explota
#  si un secret tiene valor no numérico (placeholder, texto, etc.)
# ═══════════════════════════════════════════════════════════════

import os
import sys
import logging
import requests
from pathlib import Path
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# ─── HELPERS PARA LEER ENV VARS ───────────────────────────────
def _env_int(name: str, default: int) -> int:
    """Lee una env var como entero. Si falta o tiene valor inválido usa default."""
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return int(default)
    try:
        return int(str(raw).strip())
    except ValueError:
        logger.warning(
            f"⚠️ Secret '{name}' = '{raw}' no es un número válido "
            f"— usando default: {default}"
        )
        return int(default)

def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return float(default)
    try:
        return float(str(raw).strip())
    except ValueError:
        logger.warning(f"⚠️ Secret '{name}' = '{raw}' no es float — usando {default}")
        return float(default)

def _env_str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()

# ─── VALIDACIÓN OBLIGATORIA ───────────────────────────────────
def validar_configuracion():
    requeridas = ["API_KEY", "BOT_TOKEN", "CHAT_ID"]
    faltantes  = [v for v in requeridas if not os.environ.get(v)]
    if faltantes:
        logger.critical(f"❌ Faltan variables de entorno: {', '.join(faltantes)}")
        sys.exit(1)
    logger.info("✅ Configuración validada")

# ─── SECRETS ──────────────────────────────────────────────────
API_KEY           = _env_str("API_KEY")
BOT_TOKEN         = _env_str("BOT_TOKEN")
CHAT_ID           = _env_str("CHAT_ID")
RAPIDAPI_KEY      = _env_str("RAPIDAPI_KEY")
GSHEETS_CREDS     = _env_str("GOOGLE_SERVICE_ACCOUNT_JSON")
GSHEETS_SHEET_ID  = _env_str("GOOGLE_SHEET_ID")
ANTHROPIC_API_KEY = _env_str("ANTHROPIC_API_KEY")

# ─── PARÁMETROS OPERATIVOS ────────────────────────────────────
BANKROLL           = _env_int("BANKROLL",           300_000)
SCORE_MINIMO       = _env_int("SCORE_MINIMO",       70)
SCORE_MIN_DRAW     = _env_int("SCORE_MIN_DRAW",     SCORE_MINIMO)
MIN_BOOKMAKERS_DRAW  = _env_int("MIN_BOOKMAKERS_DRAW",  3)
MIN_BOOKMAKERS_UNDER = _env_int("MIN_BOOKMAKERS_UNDER", 2)

# ─── MARTINGALA ───────────────────────────────────────────────
# Stake = BANKROLL * 2% * 1.5^(nivel-1)
# Nivel 1: ~2.0%  Nivel 2: ~3.0%  Nivel 3: ~4.5%
# Nivel 4: ~6.75% Nivel 5: ~10.1% Nivel 6: ~15.2%
MARTINGALA_BASE_PCT  = 0.02
MARTINGALA_FACTOR    = 1.5
MARTINGALA_MAX_NIVEL = 6

def calcular_stake_martingala(nivel: int) -> int:
    nivel = max(1, min(nivel, MARTINGALA_MAX_NIVEL))
    return round(BANKROLL * MARTINGALA_BASE_PCT * (MARTINGALA_FACTOR ** (nivel - 1)))

# Alias: stake nivel 1 = apuesta base
APUESTA_FIJA = calcular_stake_martingala(1)

# ─── URLs BASE ────────────────────────────────────────────────
ODDS_BASE     = "https://api.the-odds-api.com/v4"
RAPIDAPI_BASE = "https://api-football-v1.p.rapidapi.com/v3"

RAPIDAPI_MAX_H2H_POR_RUN = 11
MAX_TELEGRAM_LEN         = 3900
PRE_THRESHOLD            = 50

# ─── ARCHIVOS DE PERSISTENCIA ─────────────────────────────────
# DATA_DIR    = Path("./bot_data")
HISTORIAL_F = "historial.json"
DATA_DIR.mkdir(exist_ok=True)

# ─── SESIÓN HTTP CON RETRIES ──────────────────────────────────
def crear_sesion() -> requests.Session:
    s = requests.Session()
    retries = Retry(
        total=3, backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.mount("http://",  HTTPAdapter(max_retries=retries))
    return s

SESION = crear_sesion()

# ─── BLOQUES HORARIOS (hora Colombia UTC-5) ───────────────────
BLOQUES: dict[str, tuple[int, int]] = {
    "europa_manana":       ( 7, 10),
    "europa_media_manana": (10, 12),
    "europa_mediodia":     (12, 14),
    "europa_tarde":        (14, 17),
    "sudamerica_arranque": (17, 20),
    "sudamerica_noche":    (20, 23),
    "asia_oceania":        (23, 26),
    "asia_madrugada":      (26, 30),
}

# ─── DRAW RATES ───────────────────────────────────────────────
DRAW_RATE_DEFAULT = 0.27

DRAW_RATES: dict[str, float] = {
    "soccer_germany_bundesliga2":        0.32,
    "soccer_turkey_super_league":        0.32,
    "soccer_spain_segunda_division":     0.31,
    "soccer_scotland_premiership":       0.30,
    "soccer_italy_serie_b":              0.30,
    "soccer_france_ligue_two":           0.29,
    "soccer_spain_la_liga":              0.29,
    "soccer_poland_ekstraklasa":         0.28,
    "soccer_italy_serie_a":              0.28,
    "soccer_greece_super_league":        0.28,
    "soccer_hungary_otp_bank_liga":      0.28,
    "soccer_netherlands_eredivisie":     0.27,
    "soccer_belgium_first_div_a":        0.27,
    "soccer_france_ligue_one":           0.27,
    "soccer_efl_champ":                  0.27,
    "soccer_portugal_primeira_liga":     0.27,
    "soccer_russia_premier_league":      0.27,
    "soccer_czech_republic_liga":        0.27,
    "soccer_austria_bundesliga":         0.26,
    "soccer_germany_bundesliga":         0.26,
    "soccer_denmark_superliga":          0.26,
    "soccer_norway_eliteserien":         0.26,
    "soccer_sweden_allsvenskan":         0.26,
    "soccer_brazil_campeonato":          0.28,
    "soccer_colombia_primera_a":         0.27,
    "soccer_argentina_primera_division": 0.26,
    "soccer_chile_campeonato":           0.26,
    "soccer_uruguay_primera_division":   0.25,
    "soccer_ecuador_liga_pro":           0.25,
    "soccer_usa_mls":                    0.24,
    "soccer_mexico_ligamx":              0.25,
    "soccer_japan_j_league":             0.26,
    "soccer_south_korea_k_league1":      0.25,
    "soccer_australia_a_league":         0.24,
}

# ─── UNDER 2.5 RATES ──────────────────────────────────────────
UNDER25_RATE_DEFAULT = 0.46

UNDER25_RATES: dict[str, float] = {
    "soccer_colombia_primera_a":         0.55,
    "soccer_argentina_primera_division": 0.54,
    "soccer_chile_campeonato":           0.53,
    "soccer_poland_ekstraklasa":         0.53,
    "soccer_scotland_premiership":       0.52,
    "soccer_italy_serie_b":              0.52,
    "soccer_brazil_campeonato":          0.51,
    "soccer_uruguay_primera_division":   0.51,
    "soccer_greece_super_league":        0.51,
    "soccer_turkey_super_league":        0.50,
    "soccer_france_ligue_two":           0.50,
    "soccer_spain_segunda_division":     0.50,
    "soccer_efl_champ":                  0.49,
    "soccer_italy_serie_a":              0.49,
    "soccer_spain_la_liga":              0.48,
    "soccer_france_ligue_one":           0.48,
    "soccer_portugal_primeira_liga":     0.47,
    "soccer_russia_premier_league":      0.47,
    "soccer_hungary_otp_bank_liga":      0.47,
    "soccer_czech_republic_liga":        0.47,
    "soccer_austria_bundesliga":         0.46,
    "soccer_mexico_ligamx":              0.46,
    "soccer_ecuador_liga_pro":           0.46,
    "soccer_sweden_allsvenskan":         0.46,
    "soccer_norway_eliteserien":         0.45,
    "soccer_germany_bundesliga2":        0.45,
    "soccer_japan_j_league":             0.45,
    "soccer_south_korea_k_league1":      0.45,
    "soccer_belgium_first_div_a":        0.44,
    "soccer_denmark_superliga":          0.44,
    "soccer_usa_mls":                    0.43,
    "soccer_australia_a_league":         0.42,
    "soccer_germany_bundesliga":         0.41,
    "soccer_netherlands_eredivisie":     0.39,
}

# ─── MAPA sport_key → País ────────────────────────────────────
SPORT_KEY_PAIS: dict[str, str] = {
    "soccer_germany_bundesliga2":        "Alemania",
    "soccer_germany_bundesliga":         "Alemania",
    "soccer_turkey_super_league":        "Turquía",
    "soccer_spain_segunda_division":     "España",
    "soccer_spain_la_liga":              "España",
    "soccer_scotland_premiership":       "Escocia",
    "soccer_italy_serie_b":              "Italia",
    "soccer_italy_serie_a":              "Italia",
    "soccer_france_ligue_two":           "Francia",
    "soccer_france_ligue_one":           "Francia",
    "soccer_poland_ekstraklasa":         "Polonia",
    "soccer_netherlands_eredivisie":     "Holanda",
    "soccer_belgium_first_div_a":        "Bélgica",
    "soccer_portugal_primeira_liga":     "Portugal",
    "soccer_efl_champ":                  "Inglaterra",
    "soccer_russia_premier_league":      "Rusia",
    "soccer_austria_bundesliga":         "Austria",
    "soccer_denmark_superliga":          "Dinamarca",
    "soccer_norway_eliteserien":         "Noruega",
    "soccer_sweden_allsvenskan":         "Suecia",
    "soccer_greece_super_league":        "Grecia",
    "soccer_hungary_otp_bank_liga":      "Hungría",
    "soccer_czech_republic_liga":        "Rep. Checa",
    "soccer_brazil_campeonato":          "Brasil",
    "soccer_colombia_primera_a":         "Colombia",
    "soccer_argentina_primera_division": "Argentina",
    "soccer_chile_campeonato":           "Chile",
    "soccer_uruguay_primera_division":   "Uruguay",
    "soccer_ecuador_liga_pro":           "Ecuador",
    "soccer_mexico_ligamx":              "México",
    "soccer_usa_mls":                    "USA",
    "soccer_japan_j_league":             "Japón",
    "soccer_south_korea_k_league1":      "Corea Sur",
    "soccer_australia_a_league":         "Australia",
    "soccer_finland_veikkausliiga":      "Finlandia",
}
