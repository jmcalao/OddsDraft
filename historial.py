# ═══════════════════════════════════════════════════════════════
#  🤖 SUPERBOT v5.0 — utils.py
#  Funciones de utilidad: tiempo, texto, hashes, bloques horarios.
#  Sin dependencias de negocio — se puede importar desde cualquier módulo.
# ═══════════════════════════════════════════════════════════════

import hashlib
import unicodedata
import logging
from datetime import datetime, timezone, timedelta
from html import escape

from config import BLOQUES

logger = logging.getLogger(__name__)

MAX_TELEGRAM_LEN = 3900   # Telegram corta a 4096 — margen de seguridad


# ─── TIEMPO ───────────────────────────────────────────────────
def hora_colombia() -> datetime:
    """Retorna datetime actual en hora Colombia (UTC-5)."""
    return datetime.now(timezone.utc) - timedelta(hours=5)


def hora_local_col(commence_time_str: str) -> str:
    """Convierte ISO UTC a hora Colombia en formato HH:MM."""
    try:
        utc = datetime.fromisoformat(commence_time_str.replace("Z", "+00:00"))
        return (utc - timedelta(hours=5)).strftime("%H:%M")
    except Exception:
        return "??"


def es_hoy_y_futuro(commence_time_str: str) -> bool:
    """True si el partido es hoy en Colombia Y aún no ha comenzado."""
    try:
        utc = datetime.fromisoformat(commence_time_str.replace("Z", "+00:00"))
        col = utc - timedelta(hours=5)
        return (
            col.date() == hora_colombia().date()
            and utc > datetime.now(timezone.utc)
        )
    except Exception:
        return False


# ─── BLOQUES HORARIOS ─────────────────────────────────────────
def get_bloque_actual() -> str | None:
    """
    Retorna el nombre del bloque horario actual (hora Colombia).
    Retorna None si estamos fuera de todos los bloques definidos.
    """
    hora = hora_colombia().hour
    hora_norm = hora if hora >= 7 else hora + 24
    for nombre, (inicio, fin) in BLOQUES.items():
        if inicio <= hora_norm < fin:
            return nombre
    return None


def es_bloque_cierre() -> bool:
    """True si es el bloque nocturno de cierre (~10pm Col = asia_oceania)."""
    return get_bloque_actual() == "asia_oceania"


# ─── TEXTO Y HTML ─────────────────────────────────────────────
def safe_html(value) -> str:
    """Escapa caracteres HTML especiales para mensajes de Telegram."""
    return escape("" if value is None else str(value), quote=True)


def _normalizar(nombre: str) -> str:
    """
    Normaliza un nombre de equipo para comparaciones fuzzy:
    elimina acentos, convierte a minúsculas y colapsa espacios.
    """
    try:
        n = unicodedata.normalize("NFKD", nombre)
        n = n.encode("ASCII", "ignore").decode("ASCII")
        return " ".join(n.split()).lower().strip()
    except Exception:
        return nombre.lower().strip()


def _chunk_text(text: str, max_len: int = MAX_TELEGRAM_LEN) -> list[str]:
    """
    Divide un mensaje largo en chunks sin romper líneas.
    Garantiza que ningún chunk supere max_len caracteres.
    """
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    current = ""

    for line in text.splitlines(True):
        if len(line) > max_len:
            if current:
                chunks.append(current)
                current = ""
            for i in range(0, len(line), max_len):
                chunks.append(line[i : i + max_len])
            continue
        if len(current) + len(line) > max_len and current:
            chunks.append(current)
            current = ""
        current += line

    if current:
        chunks.append(current)

    return chunks


# ─── DEDUP ID ─────────────────────────────────────────────────
def build_alert_id(tipo: str, local: str, visitante: str,
                   sport_key: str, commence_time: str) -> str:
    """
    Genera un ID único (SHA-1 truncado) para una alerta.
    Permite detectar duplicados entre ejecuciones.
    """
    raw = "|".join([
        str(tipo or ""),
        str(sport_key or ""),
        str(commence_time or ""),
        _normalizar(local or ""),
        _normalizar(visitante or ""),
    ])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


# ─── HELPERS DE SHEETS ────────────────────────────────────────
def _nombre_mes(fecha_str: str) -> str:
    """Convierte 'YYYY-MM-DD' en nombre del mes en español."""
    meses = {
        1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
        5: "Mayo",  6: "Junio",   7: "Julio", 8: "Agosto",
        9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
    }
    try:
        return meses.get(int(fecha_str[5:7]), "Mes")
    except Exception:
        return "Mes"


def _tipo_legible(tipo: str) -> str:
    """Convierte el tipo interno al nombre visible en Google Sheets."""
    if "draw" in tipo:
        return "Empate"     # ← corregido: v4.6 tenía 'Draft' por typo
    return "Under 2.5"


def _wl_de_estado(estado: str) -> str:
    if estado == "ganada":  return "W"
    if estado == "perdida": return "L"
    return ""


def _pais_de_sport_key(sport_key: str) -> str:
    """Retorna el país inferido desde el sport_key."""
    from config import SPORT_KEY_PAIS
    for k, v in SPORT_KEY_PAIS.items():
        if k in sport_key:
            return v
    return "Internacional"
