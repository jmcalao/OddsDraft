# ═══════════════════════════════════════════════════════════════
#  🤖 SUPERBOT v5.0 — backup.py
#  Backup automático del historial.json en GitHub.
#  Corre solo en el bloque de cierre, después del análisis Claude.
#  Requiere: permissions: contents: write en el yml.
# ═══════════════════════════════════════════════════════════════

import os
import logging
import shutil
import subprocess
from config import HISTORIAL_F
from utils  import hora_colombia

logger = logging.getLogger(__name__)


def backup_historial_github() -> bool:
    """
    Hace commit del historial.json al repo de GitHub como backup.
    Usa git directamente (disponible en GitHub Actions).
    Si git no está configurado o falla, no bloquea el bot.

    Requiere en el yml:
      permissions:
        contents: write
    Y las variables de entorno automáticas de Actions:
      GITHUB_TOKEN, GITHUB_REPOSITORY
    """
    try:
        git_token = os.environ.get("GITHUB_TOKEN", "")
        git_repo  = os.environ.get("GITHUB_REPOSITORY", "")

        if not git_token or not git_repo:
            logger.info("📁 Backup GitHub: no disponible fuera de GitHub Actions")
            return False

        # Configurar identidad de git
        subprocess.run(
            ["git", "config", "user.email", "superbot@github-actions"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "SuperBot Actions"],
            check=True, capture_output=True,
        )

        # Copiar historial a la raíz del repo para que git lo vea
        shutil.copy(str(HISTORIAL_F), "historial_backup.json")

        # Verificar si hay cambios reales
        result = subprocess.run(
            ["git", "diff", "--quiet", "historial_backup.json"],
            capture_output=True,
        )
        if result.returncode == 0:
            logger.info("📁 Backup GitHub: sin cambios, nada que commitear")
            return True

        # Commit y push
        ahora_str = hora_colombia().strftime("%Y-%m-%d %H:%M")
        subprocess.run(
            ["git", "add", "historial_backup.json"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", f"backup: historial {ahora_str} Colombia"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "push"],
            check=True, capture_output=True,
        )
        logger.info("✅ Backup historial commiteado en GitHub")
        return True

    except subprocess.CalledProcessError as e:
        logger.warning(f"⚠️ Backup GitHub (git error): {e}")
        return False
    except Exception as e:
        logger.warning(f"⚠️ Backup GitHub: {type(e).__name__}: {e}")
        return False
