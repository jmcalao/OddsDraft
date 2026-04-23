# ═══════════════════════════════════════════════════════════════
#  🤖 SUPERBOT v5.0 — historial.py
#
#  FIX v5.0.2:
#  - Stats: solo cuenta tipo exactamente "draw" (no mezcla v4.x)
#  - ensure_historial: recalcula stats.pendientes desde la lista
#    real de alertas para corregir contadores desincronizados
#  - actualizar_resultados: nunca resta pendientes a negativo
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
    historial.setdefault("apostados_ids", [])
    historial.setdefault("stats", {})
    s = historial["stats"]

    # Recalcular contadores desde la lista real — evita desync
    alertas = historial["alertas"]
    s["total"]        = len(alertas)
    s["ganadas"]      = sum(1 for a in alertas if a.get("estado") == "ganada")
    s["perdidas"]     = sum(1 for a in alertas if a.get("estado") == "perdida")
    s["pendientes"]   = sum(1 for a in alertas if a.get("estado") == "pendiente")
    s["ganancia_neta"] = sum(
        a.get("ganancia_real", 0)
        for a in alertas
        if a.get("estado") in ("ganada", "perdida")
        and isinstance(a.get("ganancia_real"), (int, float))
    )

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
                data = json.load(f)
                return ensure_historial(data)
        except Exception as e:
            logger.warning(f"⚠️ Error cargando historial: {e} — iniciando vacío")
    return ensure_historial({})


def guardar_historial(historial: dict) -> None:
    try:
        historial = ensure_historial(historial)
        with open(HISTORIAL_F, "w", encoding="utf-8") as f:
            json.dump(historial, f, ensure_ascii=False, indent=2)
        logger.info(
            f"💾 Historial guardado — "
            f"{len(historial['alertas'])} alertas | "
            f"{historial['stats']['pendientes']} pendientes"
        )
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
    historial  = ensure_historial(historial)
    alert_id   = build_alert_id(tipo, local, visitante, sport_key, commence_time)
    existentes = {a.get("id") for a in historial["alertas"]}

    if alert_id in existentes:
        logger.info(f"↩️ Duplicada omitida: {tipo} | {local} vs {visitante}")
        return False

    stake = calcular_stake_martingala(nivel_martingala)
    alerta = {
        "id":                   alert_id,
        "fecha":                hora_colombia().strftime("%Y-%m-%d"),
        "tipo":                 tipo,
        "local":                local,
        "visitante":            visitante,
        "liga":                 liga,
        "sport_key":            sport_key,
        "score":                score,
        "cuota":                cuota,
        "hora_col":             hora_col,
        "commence_time":        commence_time,
        "apuesta_cop":          stake,
        "ganancia_pot":         round(stake * cuota) - stake,
        "estado":               "pendiente",
        "resultado":            None,
        "ganancia_real":        0,
        "nivel_martingala":     nivel_martingala,
        "under25_bonus":        under25_bonus,
        "cuota_under25":        cuota_under25,
        "martingala_procesado": False,
    }

    historial["alertas"].append(alerta)
    # stats se recalculan en ensure_historial al guardar
    bonus_txt = f" | U2.5+{under25_bonus}pts" if under25_bonus else ""
    logger.info(
        f"📝 Registrada: {tipo} | {local} vs {visitante} "
        f"| score:{score} | cuota:{cuota} | M:{nivel_martingala} "
        f"| stake:${stake:,}{bonus_txt}"
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
        gano          = (goles_h == goles_a)

        if gano:
            return "ganada", resultado_str, round(apuesta * alerta["cuota"]) - apuesta
        else:
            return "perdida", resultado_str, -apuesta

    return None


def actualizar_resultados(historial: dict) -> int:
    """
    Resuelve alertas pendientes. Solo modifica las alertas individuales —
    los contadores de stats se recalculan en ensure_historial al guardar.
    """
    pendientes = [a for a in historial["alertas"] if a["estado"] == "pendiente"]
    if not pendientes:
        logger.info(f"📊 Sin alertas pendientes que verificar")
        return 0

    scores_cache: dict = {}
    actualizadas = 0
    hoy    = hora_colombia().date()
    cutoff = (hoy - timedelta(days=3)).isoformat()

    for alerta in pendientes:
        if alerta.get("fecha", "9999") < cutoff:
            alerta["estado"]    = "perdida"
            alerta["resultado"] = "no_verificado"
            alerta["ganancia_real"] = -alerta.get("apuesta_cop", 0)
            actualizadas += 1
            logger.info(
                f"⏰ Timeout: {alerta['local']} vs {alerta['visitante']} → perdida"
            )
            continue

        resultado = _verificar_resultado_alerta(alerta, scores_cache)
        if resultado is None:
            continue

        estado, resultado_str, ganancia = resultado
        alerta["estado"]        = estado
        alerta["resultado"]     = resultado_str
        alerta["ganancia_real"] = ganancia
        actualizadas += 1

        logger.info(
            f"📊 Resultado: {alerta['local']} vs {alerta['visitante']} "
            f"| {resultado_str} | {estado.upper()} | "
            f"{'+' if ganancia > 0 else ''}${ganancia:,}"
        )

    return actualizadas


# ─── MARTINGALA ───────────────────────────────────────────────
def get_estado_martingala(historial: dict) -> dict:
    return ensure_historial(historial)["martingala"]


def actualizar_martingala(historial: dict) -> bool:
    historial = ensure_historial(historial)
    mart      = historial["martingala"]

    no_proc = [
        a for a in historial["alertas"]
        if a.get("estado") in ("ganada", "perdida")
        and not a.get("martingala_procesado", False)
        and a.get("tipo") == "draw"   # solo tipo exacto "draw"
    ]
    no_proc.sort(key=lambda x: (x.get("fecha", ""), x.get("hora_col", "")))

    if not no_proc:
        return False

    for alerta in no_proc:
        alerta["martingala_procesado"] = True
        if alerta["estado"] == "perdida":
            mart["racha_perdidas"] += 1
            mart["nivel"] = min(mart["racha_perdidas"] + 1, MARTINGALA_MAX_NIVEL)
            if mart["racha_perdidas"] >= MARTINGALA_MAX_NIVEL:
                mart["activa"] = False
                logger.warning(f"🛑 MARTINGALA STOP — {mart['racha_perdidas']} pérdidas")
        else:
            prev = mart["nivel"]
            mart["racha_perdidas"]  = 0
            mart["nivel"]           = 1
            mart["activa"]          = True
            mart["stop_notificado"] = False
            logger.info(f"✅ Martingala reset: ganó en nivel {prev} → vuelve a 1")

    return True


def necesita_stop_alert(historial: dict) -> bool:
    mart = historial.get("martingala", {})
    return not mart.get("activa", True) and not mart.get("stop_notificado", False)


def marcar_stop_notificado(historial: dict) -> None:
    historial["martingala"]["stop_notificado"] = True


# ─── ESTADÍSTICAS ─────────────────────────────────────────────
def calcular_stats_detalladas(historial: dict) -> dict | None:
    """
    FIX v5.0.2: Solo cuenta alertas con tipo EXACTAMENTE "draw".
    Las alertas heredadas de v4.x (doble_draw, under35, doble_under)
    no se mezclan en los conteos.
    """
    alertas = historial["alertas"]

    # Solo tipo "draw" puro — no "doble_draw" ni ninguna variante
    draws_all = [
        a for a in alertas
        if a.get("tipo") == "draw"
        and a.get("estado") in ("ganada", "perdida")
    ]

    if not draws_all:
        return None

    draws_w   = sum(1 for a in draws_all if a["estado"] == "ganada")
    draws_gan = sum(a.get("ganancia_real", 0) for a in draws_all)
    draws_stk = sum(int(a.get("apuesta_cop", 0)) for a in draws_all) or 1

    def stats_score(min_s, max_s):
        subset = [a for a in draws_all if min_s <= a.get("score", 0) < max_s]
        if not subset:
            return None
        ganadas  = sum(1 for a in subset if a["estado"] == "ganada")
        gan_neta = sum(a.get("ganancia_real", 0) for a in subset)
        staked   = sum(int(a.get("apuesta_cop", 0)) for a in subset) or 1
        return {
            "total":    len(subset),
            "ganadas":  ganadas,
            "win_rate": round(ganadas / len(subset) * 100, 1),
            "roi":      round(gan_neta / staked * 100, 1),
        }

    pendientes_draw = sum(
        1 for a in alertas
        if a.get("tipo") == "draw" and a.get("estado") == "pendiente"
    )

    return {
        "draw": {
            "total":         len(draws_all),
            "ganadas":       draws_w,
            "win_rate":      round(draws_w / len(draws_all) * 100, 1),
            "roi":           round(draws_gan / draws_stk * 100, 1),
            "ganancia_neta": draws_gan,
        },
        "score_70_79":         stats_score(70, 80),
        "score_80_89":         stats_score(80, 90),
        "score_90":            stats_score(90, 101),
        "total_resueltas":     len(draws_all),
        "pendientes_draw":     pendientes_draw,
        "ganancia_neta_total": draws_gan,
    }


def formatear_reporte_stats(stats: dict | None, pendientes_count: int) -> str:
    if not stats:
        return "📊 Sin resultados resueltos aún."

    lines = ["📊 <b>ESTADÍSTICAS ACUMULADAS</b>\n"]
    gan   = stats["ganancia_neta_total"]
    pend  = stats.get("pendientes_draw", pendientes_count)

    lines.append(
        f"🎯 Empates resueltos: {stats['total_resueltas']} | "
        f"⏳ Pendientes: {pend}"
    )
    lines.append(f"💰 Ganancia neta: {'+' if gan >= 0 else ''}${gan:,} COP\n")

    d = stats["draw"]
    lines.append(
        f"🟢 <b>Empates:</b> {d['ganadas']}/{d['total']} "
        f"({d['win_rate']}% WR | ROI {d['roi']}%)"
    )

    lines.append("\n📈 <b>Win rate por score:</b>")
    for rango, key in [
        ("70-79", "score_70_79"),
        ("80-89", "score_80_89"),
        ("90-100", "score_90"),
    ]:
        s = stats.get(key)
        if s:
            lines.append(
                f"  Score {rango}: {s['ganadas']}/{s['total']} "
                f"({s['win_rate']}% | ROI {s['roi']}%)"
            )

    return "\n".join(lines)
