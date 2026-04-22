# ═══════════════════════════════════════════════════════════════
#  🤖 SUPERBOT v5.0 — sheets.py
#
#  COLUMNAS (18):
#  A=#  B=Fecha  C=Hora  D=Tipo  E=País  F=Liga
#  G=Local  H=Visitante  I=Score  J=Resultado  K=W-L
#  L=¿Aposté?  M=Apuesta  N=Cuota  O=Potencial
#  P=G.Neta  Q=Balance  R=U2.5 Bono
#
#  ¿Aposté? = columna que vos llenás con "Y" o dejás vacía.
#  Balance y stats de Apostadas calculan SOLO sobre filas Y.
#  El bot lee los Y antes de limpiar → los conserva en historial.json
#  → los restaura al reescribir. Nunca se pierden.
#
#  Dos filas de totales al final:
#    TOTAL ALERTAS   — todas las alertas del mes
#    TOTAL APOSTADAS — solo las que marcaste Y
# ═══════════════════════════════════════════════════════════════

import json
import logging
from config import GSHEETS_CREDS, GSHEETS_SHEET_ID, BANKROLL, APUESTA_FIJA
from utils  import _nombre_mes, _tipo_legible, _wl_de_estado, _pais_de_sport_key

logger = logging.getLogger(__name__)

# ── Posición de la columna ¿Aposté? (0-indexed)
COL_APOSTADO = 11   # columna L

HEADERS = [
    "#", "Fecha", "Hora", "Tipo", "País", "Liga",
    "Local", "Visitante", "Score",
    "Resultado", "W-L", "¿Aposté?",
    "Apuesta", "Cuota", "Potencial", "G. Neta", "Balance", "U2.5 Bono",
]
# Anchos en píxeles — 18 columnas
COL_WIDTHS = [
    35,   # A #
    105,  # B Fecha
    60,   # C Hora
    80,   # D Tipo
    90,   # E País
    175,  # F Liga
    160,  # G Local
    160,  # H Visitante
    55,   # I Score
    85,   # J Resultado
    50,   # K W-L
    75,   # L ¿Aposté?
    90,   # M Apuesta
    60,   # N Cuota
    100,  # O Potencial
    100,  # P G. Neta
    105,  # Q Balance
    85,   # R U2.5 Bono
]
NCOLS = len(HEADERS)   # 18
LAST_COL = "R"


# ─── HELPERS DE FORMATO ───────────────────────────────────────
def _fmt(bold=False, fg=None, bg=None, size=10, halign="CENTER") -> dict:
    f: dict = {
        "horizontalAlignment": halign,
        "textFormat": {"bold": bold, "fontSize": size},
    }
    if fg:
        r, g, b = int(fg[0:2],16)/255, int(fg[2:4],16)/255, int(fg[4:6],16)/255
        f["textFormat"]["foregroundColor"] = {"red": r, "green": g, "blue": b}
    if bg:
        r, g, b = int(bg[0:2],16)/255, int(bg[2:4],16)/255, int(bg[4:6],16)/255
        f["backgroundColor"] = {"red": r, "green": g, "blue": b}
    return f


# ─── LEER ¿Aposté? ANTES DE LIMPIAR ──────────────────────────
def _leer_apostados_del_sheet(ws, n_filas: int) -> list[bool]:
    """
    Lee la columna L (¿Aposté?) para las n_filas de datos (sin header).
    Retorna lista de bool: True si la celda contiene "Y" (mayúscula o minúscula).
    Si falla la lectura retorna lista de False.
    """
    try:
        # fila 2 a fila n_filas+1 (fila 1 es header)
        celdas = ws.col_values(COL_APOSTADO + 1)  # 1-indexed
        # celdas[0] = header, celdas[1..n] = datos
        resultado = []
        for i in range(1, n_filas + 1):
            val = celdas[i].strip().upper() if i < len(celdas) else ""
            resultado.append(val == "Y")
        return resultado
    except Exception as e:
        logger.warning(f"⚠️ No se pudo leer ¿Aposté?: {e}")
        return [False] * n_filas


# ─── SINCRONIZACIÓN ───────────────────────────────────────────
def sincronizar_google_sheets(historial: dict) -> bool:
    if not GSHEETS_CREDS or not GSHEETS_SHEET_ID:
        logger.info("📊 Google Sheets: secrets no configurados — omitiendo")
        return False

    try:
        import gspread
        from google.oauth2.service_account import Credentials

        creds_dict = json.loads(GSHEETS_CREDS)
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        gc    = gspread.authorize(creds)
        sh    = gc.open_by_key(GSHEETS_SHEET_ID)

        alertas = historial.get("alertas", [])
        if not alertas:
            return False

        # Agrupar por mes
        por_mes: dict[str, list] = {}
        for a in alertas:
            mes = _nombre_mes(a.get("fecha", ""))
            por_mes.setdefault(mes, []).append(a)

        # Set de IDs marcados como apostados (persistido en historial)
        apostados_ids: set = set(historial.get("apostados_ids", []))

        for mes, ames in por_mes.items():
            ames_s = sorted(
                ames,
                key=lambda x: (x.get("fecha", ""), x.get("hora_col", ""))
            )
            n = len(ames_s)

            # ── Obtener o crear la pestaña ────────────────────
            try:
                ws = sh.worksheet(mes)
                # Leer ¿Aposté? ANTES de limpiar
                # (solo si la hoja ya tiene datos = al menos 2 filas)
                try:
                    filas_actuales = len(ws.get_all_values()) - 1  # sin header
                except Exception:
                    filas_actuales = 0

                if filas_actuales > 0:
                    marcados = _leer_apostados_del_sheet(ws, min(filas_actuales, n))
                    # Asociar al ID del partido por posición (mismo orden sort)
                    for idx, marcado in enumerate(marcados):
                        if idx < n and marcado:
                            apostados_ids.add(ames_s[idx].get("id", ""))

                ws.clear()
            except gspread.WorksheetNotFound:
                ws = sh.add_worksheet(title=mes, rows=500, cols=22)

        # Persistir los IDs apostados en historial para no perderlos
        historial["apostados_ids"] = list(apostados_ids)

        # ── Reescribir cada pestaña ───────────────────────────
        for mes, ames in por_mes.items():
            try:
                ws = sh.worksheet(mes)
            except gspread.WorksheetNotFound:
                ws = sh.add_worksheet(title=mes, rows=500, cols=22)

            ames_s = sorted(
                ames,
                key=lambda x: (x.get("fecha", ""), x.get("hora_col", ""))
            )
            n = len(ames_s)

            # ── 1. Construir filas ────────────────────────────
            all_rows  = [HEADERS]

            # Balance apostado: solo filas con Y resueltas
            balance_apostado = BANKROLL

            for idx, a in enumerate(ames_s, 1):
                estado   = a.get("estado", "pendiente")
                aid      = a.get("id", "")
                apostado = aid in apostados_ids  # True/False
                resultado = (a.get("resultado") or "").replace("-", "x")
                apuesta   = a.get("apuesta_cop", 0)
                cuota     = a.get("cuota", 0)
                score     = a.get("score", "")
                potencial = round(apuesta * cuota) if cuota else 0
                bonus     = a.get("under25_bonus", 0)

                # G. Neta y Balance solo para apostadas resueltas
                if apostado and estado != "pendiente":
                    gan_neta = a.get("ganancia_real", 0)
                    balance_apostado += gan_neta
                    bal = balance_apostado
                elif estado == "pendiente":
                    gan_neta = ""
                    bal = ""
                else:
                    # Resuelta pero no apostada — mostramos resultado pero sin $
                    gan_neta = ""
                    bal = ""

                wl = _wl_de_estado(estado)

                all_rows.append([
                    idx,
                    a.get("fecha", ""),
                    a.get("hora_col", ""),
                    _tipo_legible(a.get("tipo", "")),
                    _pais_de_sport_key(a.get("sport_key", "")),
                    a.get("liga", ""),
                    a.get("local", ""),
                    a.get("visitante", ""),
                    score,
                    resultado,
                    wl,
                    "Y" if apostado else "",   # ← ¿Aposté?
                    apuesta if apostado else "",
                    cuota,
                    potencial if apostado else "",
                    gan_neta,
                    bal,
                    f"+{bonus}pts" if bonus else "",
                ])

            # ── 2. Filas de totales ───────────────────────────
            total_row_alertas   = n + 2
            total_row_apostadas = n + 3

            resueltas     = [a for a in ames_s if a.get("estado") != "pendiente"]
            ganadas_all   = sum(1 for a in resueltas if a["estado"] == "ganada")
            total_all     = len(resueltas)
            wr_all        = f"{round(ganadas_all/total_all*100,1)}%" if total_all else "-"

            apostadas_res = [
                a for a in resueltas
                if a.get("id", "") in apostados_ids
            ]
            ganadas_ap    = sum(1 for a in apostadas_res if a["estado"] == "ganada")
            total_ap      = len(apostadas_res)
            wr_ap         = f"{round(ganadas_ap/total_ap*100,1)}%" if total_ap else "-"
            gan_ap        = sum(a.get("ganancia_real", 0) for a in apostadas_res)
            staked_ap     = sum(int(a.get("apuesta_cop", 0)) for a in apostadas_res) or 1
            roi_ap        = round(gan_ap / staked_ap * 100, 1) if apostadas_res else 0

            # Fila TOTAL ALERTAS (todas)
            all_rows.append([
                "", f"TOTAL ALERTAS — {mes}",
                f"{ganadas_all}W / {total_all - ganadas_all}L",
                f"WR {wr_all}", "", "", "", "", "", "", "", "",
                sum(a.get("apuesta_cop",0) for a in ames_s if a.get("id","") in apostados_ids),
                "", "", "", "", "",
            ])

            # Fila TOTAL APOSTADAS (solo Y)
            all_rows.append([
                "", f"APOSTADAS — {mes}",
                f"{ganadas_ap}W / {total_ap - ganadas_ap}L",
                f"WR {wr_ap}", f"ROI {roi_ap}%",
                "", "", "", "", "", "", "",
                staked_ap if apostadas_res else "",
                "", "", gan_ap or "", balance_apostado, "",
            ])

            ws.update("A1", all_rows, value_input_option="USER_ENTERED")

            # ── 3. Formatos ───────────────────────────────────
            ws.freeze(rows=1)
            fmt_reqs = []

            # Header
            fmt_reqs.append({
                "range": f"A1:{LAST_COL}1",
                "format": _fmt(bold=True, fg="FFFFFF", bg="1F3864", size=10),
            })
            # Fila TOTAL ALERTAS
            fmt_reqs.append({
                "range": f"A{total_row_alertas}:{LAST_COL}{total_row_alertas}",
                "format": _fmt(bold=True, fg="FFFFFF", bg="4A86C8", size=10),
            })
            # Fila APOSTADAS — verde oscuro
            fmt_reqs.append({
                "range": f"A{total_row_apostadas}:{LAST_COL}{total_row_apostadas}",
                "format": _fmt(bold=True, fg="FFFFFF", bg="274E13", size=10),
            })

            # Filas de datos
            for ri, a in enumerate(ames_s, 2):
                estado   = a.get("estado", "pendiente")
                apostado = a.get("id", "") in apostados_ids

                if estado == "ganada":
                    bg = "D9EAD3"      # verde suave
                elif estado == "perdida":
                    bg = "F4CCCC"      # rojo suave
                else:
                    bg = "F8F9FA" if ri % 2 == 0 else "FFFFFF"

                # Fila completa base
                fmt_reqs.append({
                    "range": f"A{ri}:{LAST_COL}{ri}",
                    "format": _fmt(bg=bg, size=9, halign="CENTER"),
                })

                # Score — bold
                fmt_reqs.append({
                    "range": f"I{ri}",
                    "format": _fmt(bold=True, bg=bg, size=9),
                })

                # Tipo — azul bold
                fmt_reqs.append({
                    "range": f"D{ri}",
                    "format": _fmt(bold=True, fg="1155CC", bg=bg, size=9),
                })

                # W-L
                wl = _wl_de_estado(estado)
                if wl == "W":
                    fmt_reqs.append({
                        "range": f"K{ri}",
                        "format": _fmt(bold=True, fg="274E13", bg=bg, size=9),
                    })
                elif wl == "L":
                    fmt_reqs.append({
                        "range": f"K{ri}",
                        "format": _fmt(bold=True, fg="CC0000", bg=bg, size=9),
                    })

                # ¿Aposté? — fondo amarillo si Y, gris si vacío
                if apostado:
                    fmt_reqs.append({
                        "range": f"L{ri}",
                        "format": _fmt(bold=True, fg="7F4F00", bg="FFE599", size=9),
                    })
                else:
                    fmt_reqs.append({
                        "range": f"L{ri}",
                        "format": _fmt(fg="999999", bg="F3F3F3", size=9),
                    })

            # Balance apostado — color según esté arriba/abajo del bankroll
            _bal = BANKROLL
            for ri, a in enumerate(ames_s, 2):
                aid = a.get("id", "")
                if a.get("estado") == "pendiente" or aid not in apostados_ids:
                    continue
                gan = a.get("ganancia_real", 0)
                if isinstance(gan, (int, float)):
                    _bal += gan
                bg_row = "D9EAD3" if a.get("estado") == "ganada" else "F4CCCC"
                fg_bal = "CC0000" if _bal < BANKROLL else "274E13"
                fmt_reqs.append({
                    "range": f"Q{ri}",
                    "format": _fmt(bold=True, fg=fg_bal, bg=bg_row, size=9),
                })

            try:
                ws.batch_format(fmt_reqs)
            except AttributeError:
                logger.warning("⚠️ batch_format no soportado — actualiza gspread")

            # ── 4. Anchos de columna ──────────────────────────
            dim_reqs = []
            for ci, width in enumerate(COL_WIDTHS):
                dim_reqs.append({
                    "updateDimensionProperties": {
                        "range": {
                            "sheetId": ws.id,
                            "dimension": "COLUMNS",
                            "startIndex": ci,
                            "endIndex": ci + 1,
                        },
                        "properties": {"pixelSize": width},
                        "fields": "pixelSize",
                    }
                })
            dim_reqs.append({
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": ws.id,
                        "dimension": "ROWS",
                        "startIndex": 0,
                        "endIndex": 1,
                    },
                    "properties": {"pixelSize": 30},
                    "fields": "pixelSize",
                }
            })
            sh.batch_update({"requests": dim_reqs})

        logger.info(
            f"✅ Google Sheets sincronizado: "
            f"{len(alertas)} alertas | {len(apostados_ids)} apostadas | "
            f"{len(por_mes)} mes(es)"
        )
        return True

    except ImportError:
        logger.warning("⚠️ gspread no instalado — agrega a requirements.txt")
        return False
    except json.JSONDecodeError:
        logger.error("❌ GOOGLE_SERVICE_ACCOUNT_JSON inválido")
        return False
    except Exception as e:
        logger.error(f"❌ Error Google Sheets: {type(e).__name__}: {e}")
        return False
