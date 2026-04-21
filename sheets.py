# ═══════════════════════════════════════════════════════════════
#  🤖 SUPERBOT v5.0 — sheets.py
#  Sincronización del historial con Google Sheets.
#  Una pestaña por mes, formato visual premium, colores W/L.
# ═══════════════════════════════════════════════════════════════

import json
import logging
from config import GSHEETS_CREDS, GSHEETS_SHEET_ID, BANKROLL, APUESTA_FIJA
from utils  import _nombre_mes, _tipo_legible, _wl_de_estado, _pais_de_sport_key

logger = logging.getLogger(__name__)


# ─── HELPERS DE FORMATO ───────────────────────────────────────
def _fmt(bold=False, fg=None, bg=None, size=10, halign="CENTER") -> dict:
    """Construye un dict de formato para Google Sheets API."""
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


# ─── SINCRONIZACIÓN ───────────────────────────────────────────
def sincronizar_google_sheets(historial: dict) -> bool:
    """
    Sincroniza el historial con Google Sheets en tiempo real.

    ESTRUCTURA: una pestaña por mes, ordenada por fecha+hora.
    COLUMNAS: #, Fecha, Hora, Tipo, País, Liga, Local, Visitante,
              Resultado, W-L, Apuesta, Cuota, Potencial, G.Neta,
              Balance, U2.5 Bono
    COLORES:
      Header:    azul oscuro #1F3864, texto blanco
      Ganada:    verde suave #D9EAD3
      Perdida:   rojo suave  #F4CCCC
      Pendiente: gris alterno #F8F9FA / #FFFFFF
      Totales:   azul medio  #4A86C8, texto blanco, bold
      Balance positivo: texto verde | negativo: texto rojo
    """
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

        # ── Columnas (v5: agrega U2.5 Bono) ──────────────────
        HEADERS = [
            "#", "Fecha", "Hora", "Tipo", "País", "Liga",
            "Local", "Visitante",
            "Resultado", "W-L", "Apuesta", "Cuota",
            "Potencial", "G. Neta", "Balance", "U2.5 Bono",
        ]
        # Anchos en píxeles — 16 columnas
        COL_WIDTHS = [40, 105, 65, 85, 95, 180, 165, 165,
                      90, 55, 90, 65, 105, 110, 110, 90]

        for mes, ames in por_mes.items():
            # Obtener o crear la pestaña
            try:
                ws = sh.worksheet(mes)
                ws.clear()
            except gspread.WorksheetNotFound:
                ws = sh.add_worksheet(title=mes, rows=500, cols=20)

            ames_s = sorted(
                ames,
                key=lambda x: (x.get("fecha", ""), x.get("hora_col", ""))
            )
            n = len(ames_s)

            # ── 1. Construir filas de datos ───────────────────
            all_rows = [HEADERS]
            balance_acum = BANKROLL  # empieza desde bankroll inicial

            for idx, a in enumerate(ames_s, 1):
                estado    = a.get("estado", "pendiente")
                resultado = (a.get("resultado") or "").replace("-", "x")
                apuesta   = a.get("apuesta_cop", 0)
                cuota     = a.get("cuota", 0)
                potencial = round(apuesta * cuota) if cuota else 0
                gan_neta  = (
                    a.get("ganancia_real", 0)
                    if estado != "pendiente" else ""
                )
                wl    = _wl_de_estado(estado)
                bonus = a.get("under25_bonus", 0)

                if isinstance(gan_neta, (int, float)):
                    balance_acum += gan_neta
                bal = balance_acum if estado != "pendiente" else ""

                all_rows.append([
                    idx,
                    a.get("fecha", ""),
                    a.get("hora_col", ""),
                    _tipo_legible(a.get("tipo", "")),
                    _pais_de_sport_key(a.get("sport_key", "")),
                    a.get("liga", ""),
                    a.get("local", ""),
                    a.get("visitante", ""),
                    resultado, wl, apuesta, cuota, potencial,
                    gan_neta, bal,
                    f"+{bonus}pts" if bonus else "",
                ])

            # Fila de totales
            resueltas   = [a for a in ames_s if a.get("estado") != "pendiente"]
            ganadas_tot = sum(1 for a in resueltas if a.get("estado") == "ganada")
            total_r     = len(resueltas)
            wr_pct      = f"{round(ganadas_tot/total_r*100,1)}%" if total_r > 0 else "-"
            gan_neta_tot = sum(
                a.get("ganancia_real", 0)
                for a in ames_s
                if a.get("estado") != "pendiente"
            )
            roi_pct = (
                round(gan_neta_tot / (total_r * APUESTA_FIJA) * 100, 1)
                if total_r > 0 else 0
            )
            total_row = n + 2

            all_rows.append([
                "", f"TOTALES — {mes}",
                f"{ganadas_tot}W / {total_r - ganadas_tot}L",
                f"WR {wr_pct}", f"ROI {roi_pct}%",
                "", "", "", "",
                sum(a.get("apuesta_cop", 0) for a in ames_s), "",
                sum(round(a.get("apuesta_cop", 0)*a.get("cuota", 0)) for a in ames_s),
                gan_neta_tot,
                balance_acum,
                "",
            ])

            ws.update("A1", all_rows, value_input_option="USER_ENTERED")

            # ── 2. Formatos ───────────────────────────────────
            ws.freeze(rows=1)
            fmt_reqs = []

            # Header y totales
            fmt_reqs.append({
                "range": "A1:P1",
                "format": _fmt(bold=True, fg="FFFFFF", bg="1F3864", size=10),
            })
            fmt_reqs.append({
                "range": f"A{total_row}:P{total_row}",
                "format": _fmt(bold=True, fg="FFFFFF", bg="4A86C8", size=10),
            })

            # Filas de datos
            for ri, a in enumerate(ames_s, 2):
                estado = a.get("estado", "pendiente")
                if estado == "ganada":
                    bg = "D9EAD3"
                elif estado == "perdida":
                    bg = "F4CCCC"
                else:
                    bg = "F8F9FA" if ri % 2 == 0 else "FFFFFF"

                fmt_reqs.append({
                    "range": f"A{ri}:P{ri}",
                    "format": _fmt(bg=bg, size=9, halign="CENTER"),
                })

                # Columna Tipo con color especial
                fmt_reqs.append({
                    "range": f"D{ri}",
                    "format": _fmt(bold=True, fg="1155CC", bg=bg, size=9),
                })

                # Columna W-L
                wl = _wl_de_estado(estado)
                if wl == "W":
                    fmt_reqs.append({
                        "range": f"J{ri}",
                        "format": _fmt(bold=True, fg="274E13", bg=bg, size=9),
                    })
                elif wl == "L":
                    fmt_reqs.append({
                        "range": f"J{ri}",
                        "format": _fmt(bold=True, fg="CC0000", bg=bg, size=9),
                    })

            # Columna Balance: rojo si por debajo del bankroll inicial
            _bal = BANKROLL
            for ri, a in enumerate(ames_s, 2):
                if a.get("estado") == "pendiente":
                    continue
                gan = a.get("ganancia_real", 0)
                if isinstance(gan, (int, float)):
                    _bal += gan
                bg     = "D9EAD3" if a.get("estado") == "ganada" else "F4CCCC"
                fg_bal = "CC0000" if _bal < BANKROLL else "274E13"
                fmt_reqs.append({
                    "range": f"O{ri}",
                    "format": _fmt(bold=True, fg=fg_bal, bg=bg, size=9),
                })

            # Enviar formatos en batch
            try:
                ws.batch_format(fmt_reqs)
            except AttributeError:
                logger.warning("⚠️ batch_format no soportado — actualiza gspread")

            # Anchos de columna
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
            # Altura del header
            dim_reqs.append({
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": ws.id,
                        "dimension": "ROWS",
                        "startIndex": 0,
                        "endIndex": 1,
                    },
                    "properties": {"pixelSize": 28},
                    "fields": "pixelSize",
                }
            })
            sh.batch_update({"requests": dim_reqs})

        logger.info(
            f"✅ Google Sheets sincronizado: "
            f"{len(alertas)} alertas | {len(por_mes)} mes(es)"
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
