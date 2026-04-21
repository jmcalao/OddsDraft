# ═══════════════════════════════════════════════════════════════
#  🤖 SUPERBOT v5.0 — scoring.py
#
#  Lógica de puntuación para alertas de EMPATE.
#
#  CAMBIO v5 vs v4.6:
#  • Under 3.5 eliminado como mercado independiente.
#  • Under 2.5 ahora es un BONUS al score_draw.
#    Si un partido tiene score_draw ≥ 70 Y Under 2.5 con EV positivo,
#    se suman puntos adicionales al score_draw.
#  • Resultado: solo hay un tipo de alerta (draw), más enriquecida.
# ═══════════════════════════════════════════════════════════════

import logging
from config import DRAW_RATE_DEFAULT, UNDER25_RATE_DEFAULT, UNDER25_RATES

logger = logging.getLogger(__name__)


# ─── SCORE EMPATE ─────────────────────────────────────────────
def calcular_score_draw(
    draw_rate: float,
    cuota_loc: float | None,
    cuota_emp: float,
    cuota_vis: float | None,
    num_bm: int,
) -> tuple[int, list[str], list[str], int, int]:
    """
    Calcula el score de empate (0-100).

    Retorna: (score, razones, rechazos, prob_real_pct, prob_imp_pct)
    - score:       puntuación final 0-100
    - razones:     lista de strings explicando cada componente
    - rechazos:    si no está vacía, el partido debe descartarse
    - prob_real:   probabilidad real de empate (%) según draw_rate ajustado
    - prob_imp:    probabilidad implícita (%) según cuota del mercado

    COMPONENTES DEL SCORE:
    1. Liga (hasta 20 pts)       — draw_rate histórico
    2. Equilibrio (hasta 25 pts) — ratio entre cuotas local/visitante
    3. Cuota empate (hasta 20 pts)— rango ideal 2.9-3.4
    4. Expected Value (hasta 25 pts)— prob_real vs prob_implícita
    5. Bookmakers (hasta 10 pts) — consenso del mercado
    """
    try:
        score: int     = 0
        razones: list  = []
        rechazos: list = []

        # ── Filtros de rechazo ────────────────────────────────
        if cuota_loc and cuota_loc < 2.00:
            rechazos.append(f"favorito local ({cuota_loc})")
            return 0, [], rechazos, 0, 0

        if cuota_vis and cuota_vis < 2.00:
            rechazos.append(f"favorito visitante ({cuota_vis})")
            return 0, [], rechazos, 0, 0

        if cuota_loc and cuota_vis:
            ratio = max(cuota_loc, cuota_vis) / min(cuota_loc, cuota_vis)
            if ratio > 2.2:
                rechazos.append(f"desequilibrio muy alto ({round(ratio, 1)}x)")
                return 0, [], rechazos, 0, 0

        # ── 1. Liga ───────────────────────────────────────────
        liga_pts = min(round(draw_rate * 65), 20)
        score   += liga_pts
        razones.append(
            f"Liga {round(draw_rate * 100)}% empates "
            f"{'(conocida)' if draw_rate != DRAW_RATE_DEFAULT else '(promedio global)'}: "
            f"+{liga_pts}"
        )

        # ── 2. Equilibrio de cuotas ───────────────────────────
        if cuota_loc and cuota_vis:
            ratio = max(cuota_loc, cuota_vis) / min(cuota_loc, cuota_vis)
            if   ratio <= 1.15: ep, et = 25, "muy parejos"
            elif ratio <= 1.40: ep, et = 20, "bastante parejos"
            elif ratio <= 1.70: ep, et = 13, "leve diferencia"
            elif ratio <= 2.20: ep, et =  6, "diferencia moderada"
            else:               ep, et =  0, "diferencia alta"
            score   += ep
            razones.append(f"Equilibrio ({et}, ratio {round(ratio, 2)}x): +{ep}")

        # ── 3. Cuota empate ───────────────────────────────────
        if   2.90 <= cuota_emp <= 3.40: rp, rt = 20, "ideal 2.9-3.4"
        elif 2.70 <= cuota_emp <  2.90: rp, rt = 14, "bueno 2.7-2.9"
        elif 3.40 <  cuota_emp <= 3.80: rp, rt = 12, "aceptable 3.4-3.8"
        elif 2.50 <= cuota_emp <  2.70: rp, rt =  7, "bajo 2.5-2.7"
        elif 3.80 <  cuota_emp <= 4.20: rp, rt =  5, "alto 3.8-4.2"
        else:                           rp, rt =  0, "fuera de rango"
        score   += rp
        razones.append(f"Cuota empate {cuota_emp} ({rt}): +{rp}")

        # ── 4. Expected Value ─────────────────────────────────
        prob_real = draw_rate * 1.15   # ajuste de mercado (+15%)
        prob_imp  = 1.0 / cuota_emp
        ev        = prob_real - prob_imp

        if   ev > 0.07: ep2, et2 = 25, "valor excelente"
        elif ev > 0.04: ep2, et2 = 18, "buen valor"
        elif ev > 0.02: ep2, et2 = 12, "valor positivo"
        elif ev > 0:    ep2, et2 =  6, "valor marginal"
        else:           ep2, et2 =  0, "sin valor"
        score   += ep2
        razones.append(
            f"EV {'+' if ev > 0 else ''}{round(ev * 100, 1)}% ({et2}): +{ep2}"
        )

        # ── 5. Bookmakers ─────────────────────────────────────
        if   num_bm >= 10: bp, bt = 10, "alto consenso"
        elif num_bm >=  6: bp, bt =  7, "buen consenso"
        elif num_bm >=  3: bp, bt =  4, "consenso parcial"
        else:              bp, bt =  1, "pocos datos"
        score   += bp
        razones.append(f"{num_bm} bookmakers ({bt}): +{bp}")

        return (
            min(score, 100),
            razones,
            rechazos,
            round(prob_real * 100),
            round(prob_imp  * 100),
        )

    except Exception as e:
        logger.error(f"❌ calcular_score_draw: {e}")
        return 0, [], [f"error: {e}"], 0, 0


# ─── BONUS UNDER 2.5 ──────────────────────────────────────────
def calcular_bonus_under25(
    under25_rate: float,
    cuota_under25: float,
    score_draw_actual: int,
) -> tuple[int, str | None]:
    """
    Calcula un bonus de puntos para el score_draw basado en Under 2.5.

    Solo aplica si:
    - score_draw_actual >= 70 (partido ya es candidato)
    - cuota_under25 en rango razonable (1.50 – 2.30)
    - EV Under 2.5 es positivo

    Retorna: (bonus_pts, razon_str)
    - bonus_pts: puntos a sumar al score_draw (0 si no aplica)
    - razon_str: texto para mostrar en el mensaje (None si no aplica)

    Lógica: Under 2.5 en un partido equilibrado es una señal de que
    ambos equipos están en un contexto de partido cerrado, lo cual
    correlaciona positivamente con el empate.
    """
    # No aplicar si el draw score no llega al umbral mínimo
    if score_draw_actual < 70:
        return 0, None

    # Rango de cuota válido para Under 2.5
    if not (1.50 <= cuota_under25 <= 2.30):
        return 0, None

    try:
        prob_real = under25_rate * 1.10   # ajuste +10%
        prob_imp  = 1.0 / cuota_under25
        ev        = prob_real - prob_imp

        if ev <= 0:
            return 0, None

        # Escala de bonus
        if   ev > 0.10: bonus, etiqueta = 12, "EV excelente U2.5"
        elif ev > 0.07: bonus, etiqueta = 10, "muy buen EV U2.5"
        elif ev > 0.04: bonus, etiqueta =  7, "buen EV U2.5"
        elif ev > 0.02: bonus, etiqueta =  4, "EV positivo U2.5"
        else:           bonus, etiqueta =  2, "EV marginal U2.5"

        ur_tag = (
            "(conocida)" if under25_rate != UNDER25_RATE_DEFAULT
            else "(promedio global)"
        )
        razon = (
            f"⚡ Under 2.5 {ur_tag} — cuota {cuota_under25} | "
            f"real:{round(prob_real*100)}% vs impl:{round(prob_imp*100)}% "
            f"({etiqueta}): +{bonus}"
        )
        return bonus, razon

    except Exception as e:
        logger.warning(f"⚠️ calcular_bonus_under25: {e}")
        return 0, None
