# ═══════════════════════════════════════════════════════════════
#  🤖 SUPERBOT v5.0 — historial.py
#  CRUD del historial JSON, dedup, stats, verificación de resultados
#  y gestión del estado de Martingala.
# ═══════════════════════════════════════════════════════════════

import json
import logging
import time
from datetime import timedelta

from config import (
    HISTORIAL_F, BANKROLL, ODDS_BASE, SESION, API_KEY,
    calcular_stake_martingala, MARTINGALA_MAX_NIVEL,
)
from utils import build_alert_id, hora_colombia, _normalizar

logger = logging.getLogger(__name__)

# ─── ESTADO MARTINGALA POR DEFECTO ────────────────────────────
_MART_DEFAULT = {
    "nivel":           1,
    "racha_perdidas":  0,
    "activa":          True,
    "stop_notificado": False,
}

# ─── ESTRUCTURA BASE ──────────────────────────────────────────
def ensure_historial(historial) -> dict:
    if not isinstance(historial, dict):
        historial = {}
    historial.setdefault("alertas", [])
    historial.setdefault("stats", {})
    s = historial["stats"]
    s.setdefault("total",         0)
    s.setdefault("ganadas",       0)
    s.setdefault("perdidas",      0)
    s.setdefault("pendientes",    0)
    s.setdefault("ganancia_neta", 0)
    # Martingala: migra historial antiguo sin este campo
    if "martingala" not in historial:
        historial["martingala"] = dict(_MART_DEFAULT)
    else:
        for k, v in _MART_DEFAULT.items():
            historial["martingala"].setdefault(k, v)
    return historial


# ─── CARGA Y GUARDADO ─────────────────────────────────────────
def cargar_historial() -> dict:
    if HISTORIAL_F.exists():
        try:
            with open(HISTORIAL_F, "r", encoding="utf-8") as f:
                return ensure_historial(json.load(f))
        except Exception as e:
            logger.warning(f"⚠️ Error cargando historial: {e} — iniciando vacío")
    return ensure_historial({})


def guardar_historial(historial: dict) -> None:
    try:
        historial = ensure_historial(historial)
        with open(HISTORIAL_F, "w", encoding="utf-8") as f:
            json.dump(historial, f, ensure_ascii=False, indent=2)
        logger.info(f"💾 Historial guardado ({len(historial['alertas'])} alertas)")
    except Exception as e:
        logger.error(f"❌ Error guardando historial: {e}")


# ─── REGISTRO DE ALERTAS ──────────────────────────────────────
def registrar_alerta(
    historial: dict,
    tipo: str,
    local: str,
    visitante: str,
    liga: str,
    score: int,
    cuota: float,
    hora_col: str,
    sport_key: str,
    commence_time: str,
    nivel_martingala: int = 1,
    under25_bonus: int = 0,
    cuota_under25: float | None = None,
) -> bool:
    """
    Registra una nueva alerta evitando duplicados.
    Retorna True si se registró, False si ya existía.
    """
    historial  = ensure_historial(historial)
    alert_id   = build_alert_id(tipo, local, visitante, sport_key, commence_time)
    existentes = {a.get("id") for a in historial["alertas"]}

    if alert_id in existentes:
        logger.info(f"↩️ Duplicada omitida: {tipo} | {local} vs {visitante}")
        return False

    stake = calcular_stake_martingala(nivel_martingala)

    alerta = {
        "id":                alert_id,
        "fecha":             hora_colombia().strftime("%Y-%m-%d"),
        "tipo":              tipo,
        "local":             local,
        "visitante":         visitante,
        "liga":              liga,
        "sport_key":         sport_key,
        "score":             score,
        "cuota":             cuota,
        "hora_col":          hora_col,
        "commence_time":     commence_time,
        "apuesta_cop":       stake,
        "ganancia_pot":      round(stake * cuota) - stake,
        "estado":            "pendiente",
        "resultado":         None,
        "ganancia_real":     0,
        "nivel_martingala":  nivel_martingala,
        "under25_bonus":     under25_bonus,
        "cuota_under25":     cuota_under25,
        # Flag para que la martingala no procese dos veces la misma alerta
        "martingala_procesado": False,
    }

    historial["alertas"].append(alerta)
    historial["stats"]["total"]      += 1
    historial["stats"]["pendientes"] += 1

    bonus_txt = f" | U2.5 bonus:+{under25_bonus}pts" if under25_bonus else ""
    logger.info(
        f"📝 Registrada: {tipo} | {local} vs {visitante} "
        f"| cuota:{cuota} | nivel M:{nivel_martingala} | stake:${stake:,}{bonus_txt}"
    )
    return True


# ─── VERIFICACIÓN DE RESULTADOS ───────────────────────────────
def _get_scores_finalizados(sport_key: str) -> list:
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


def _verificar_resultado_alerta(alerta: dict, scores_cache: dict) -> tuple | None:
    sport_key = alerta.get("sport_key", "")
    local_n   = _normalizar(alerta["local"])
    visit_n   = _normalizar(alerta["visitante"])

    if sport_key not in scores_cache:
        scores_cache[sport_key] = _get_scores_finalizados(sport_key)
        time.sleep(0.3)

    for score_data in scores_cache.get(sport_key, []):
        if not score_data.get("completed", False):
            continue

        h_name = _normalizar(score_data.get("home_team", ""))
        a_name = _normalizar(score_data.get("away_team", ""))

        if not (local_n in h_name or h_name in local_n):
            continue
        if not (visit_n in a_name or a_name in visit_n):
            continue

        scores  = score_data.get("scores") or []
        goles_h = goles_a = None
        for s in scores:
            try:
                if s.get("name") == score_data.get("home_team"):
                    goles_h = int(s["score"])
                else:
                    goles_a = int(s["score"])
            except (KeyError, ValueError):
                pass

        if goles_h is None or goles_a is None:
            continue

        resultado_str = f"{goles_h}-{goles_a}"
        apuesta       = alerta["apuesta_cop"]
        gano          = (goles_h == goles_a)   # draw gana si empata

        if gano:
            return "ganada", resultado_str, round(apuesta * alerta["cuota"]) - apuesta
        else:
            return "perdida", resultado_str, -apuesta

    return None


def actualizar_resultados(historial: dict) -> int:
    """
    Resuelve alertas pendientes contra The Odds API.
    Retorna cuántas alertas se actualizaron.
    """
    pendientes = [a for a in historial["alertas"] if a["estado"] == "pendiente"]
    if not pendientes:
        return 0

    scores_cache: dict = {}
    actualizadas = 0
    hoy    = hora_colombia().date()
    cutoff = (hoy - timedelta(days=3)).isoformat()

    for alerta in pendientes:
        if alerta.get("fecha", "9999") < cutoff:
            alerta["estado"]    = "perdida"
            alerta["resultado"] = "no_verificado"
            historial["stats"]["pendientes"] -= 1
            historial["stats"]["perdidas"]   += 1
            actualizadas += 1
            continue

        resultado = _verificar_resultado_alerta(alerta, scores_cache)
        if resultado is None:
            continue

        estado, resultado_str, ganancia = resultado
        alerta["estado"]        = estado
        alerta["resultado"]     = resultado_str
        alerta["ganancia_real"] = ganancia

        historial["stats"]["pendientes"] -= 1
        if estado == "ganada":
            historial["stats"]["ganadas"]      += 1
            historial["stats"]["ganancia_neta"] += ganancia
        else:
            historial["stats"]["perdidas"]      += 1
            historial["stats"]["ganancia_neta"] += ganancia

        logger.info(
            f"📊 Resultado: {alerta['local']} vs {alerta['visitante']} "
            f"| {resultado_str} | {estado.upper()} | "
            f"{'+' if ganancia > 0 else ''}${ganancia:,}"
        )
        actualizadas += 1

    return actualizadas


# ─── MARTINGALA ───────────────────────────────────────────────
def get_estado_martingala(historial: dict) -> dict:
    """Retorna el estado actual de la martingala (nivel, racha, activa)."""
    return ensure_historial(historial)["martingala"]


def actualizar_martingala(historial: dict) -> bool:
    """
    Actualiza el estado de la martingala procesando los resultados
    de las alertas que aún no se han contabilizado (martingala_procesado=False).

    Lógica:
    - Pérdida → racha++ ; nivel = min(racha+1, 6)
    - 6 pérdidas consecutivas → activa=False (STOP)
    - Ganancia → reset total: racha=0, nivel=1, activa=True

    Retorna True si el estado cambió.
    """
    historial = ensure_historial(historial)
    mart      = historial["martingala"]

    # Alertas resueltas no procesadas, ordenadas por fecha+hora
    no_proc = [
        a for a in historial["alertas"]
        if a.get("estado") in ("ganada", "perdida")
        and not a.get("martingala_procesado", False)
        and "draw" in a.get("tipo", "")
    ]
    no_proc.sort(key=lambda x: (x.get("fecha", ""), x.get("hora_col", "")))

    if not no_proc:
        return False

    for alerta in no_proc:
        alerta["martingala_procesado"] = True
        if alerta["estado"] == "perdida":
            mart["racha_perdidas"] += 1
            nuevo_nivel = mart["racha_perdidas"] + 1
            mart["nivel"] = min(nuevo_nivel, MARTINGALA_MAX_NIVEL)
            if mart["racha_perdidas"] >= MARTINGALA_MAX_NIVEL:
                mart["activa"] = False
                logger.warning(
                    f"🛑 MARTINGALA STOP — {mart['racha_perdidas']} "
                    f"pérdidas consecutivas"
                )
        else:   # ganada
            prev_nivel = mart["nivel"]
            mart["racha_perdidas"]  = 0
            mart["nivel"]           = 1
            mart["activa"]          = True
            mart["stop_notificado"] = False
            logger.info(
                f"✅ Martingala reset: ganó en nivel {prev_nivel} → vuelve a 1"
            )

    return True


def necesita_stop_alert(historial: dict) -> bool:
    """True si hay que enviar el mensaje de STOP (una sola vez)."""
    mart = historial.get("martingala", {})
    return not mart.get("activa", True) and not mart.get("stop_notificado", False)


def marcar_stop_notificado(historial: dict) -> None:
    historial["martingala"]["stop_notificado"] = True


# ─── ESTADÍSTICAS ─────────────────────────────────────────────
def calcular_stats_detalladas(historial: dict) -> dict | None:
    alertas   = historial["alertas"]
    resueltas = [a for a in alertas if a["estado"] in ("ganada", "perdida")]

    if not resueltas:
        return None

    def stats_score(min_s, max_s):
        subset  = [a for a in resueltas if min_s <= a["score"] < max_s]
        if not subset:
            return None
        ganadas  = sum(1 for a in subset if a["estado"] == "ganada")
        gan_neta = sum(a["ganancia_real"] for a in subset)
        staked   = sum(int(a.get("apuesta_cop", 0)) for a in subset) or 1
        return {
            "total":    len(subset),
            "ganadas":  ganadas,
            "win_rate": round(ganadas / len(subset) * 100, 1),
            "roi":      round(gan_neta / staked * 100, 1),
        }

    draws_all = [a for a in resueltas if "draw" in a.get("tipo", "")]
    draws_w   = sum(1 for a in draws_all if a["estado"] == "ganada")
    draws_gan = sum(a["ganancia_real"] for a in draws_all)
    draws_stk = sum(int(a.get("apuesta_cop", 0)) for a in draws_all) or 1

    return {
        "draw": {
            "total":        len(draws_all),
            "ganadas":      draws_w,
            "win_rate":     round(draws_w / len(draws_all) * 100, 1) if draws_all else 0,
            "roi":          round(draws_gan / draws_stk * 100, 1) if draws_all else 0,
            "ganancia_neta": draws_gan,
        } if draws_all else None,
        "score_70_79":  stats_score(70, 80),
        "score_80_89":  stats_score(80, 90),
        "score_90":     stats_score(90, 101),
        "total_resueltas":     len(resueltas),
        "ganancia_neta_total": historial["stats"]["ganancia_neta"],
    }


def formatear_reporte_stats(stats: dict | None, pendientes_count: int) -> str:
    if not stats:
        return "📊 Sin resultados resueltos aún."

    lines = ["📊 <b>ESTADÍSTICAS ACUMULADAS</b>\n"]
    gan   = stats["ganancia_neta_total"]
    lines.append(
        f"🎯 Total resueltas: {stats['total_resueltas']} | "
        f"⏳ Pendientes: {pendientes_count}"
    )
    lines.append(f"💰 Ganancia neta: {'+' if gan >= 0 else ''}${gan:,} COP\n")

    if stats.get("draw"):
        d = stats["draw"]
        lines.append(
            f"🟢 <b>Empates:</b> {d['ganadas']}/{d['total']} "
            f"({d['win_rate']}% WR | ROI {d['roi']}%)"
        )

    lines.append("\n📈 <b>Win rate por score:</b>")
    for rango, key in [("70-79","score_70_79"),("80-89","score_80_89"),("90-100","score_90")]:
        s = stats.get(key)
        if s:
            lines.append(f"  Score {rango}: {s['ganadas']}/{s['total']} ({s['win_rate']}%)")

    return "\n".join(lines)
