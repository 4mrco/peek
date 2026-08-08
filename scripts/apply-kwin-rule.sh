#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# Injeta uma KWin Rule no kwinrulesrc para forçar o posicionamento
# e comportamento da janela do PEEK no Wayland.
#
# Re-executar é seguro: usa um ID fixo, sobrescreve a regra anterior.
#
# Uso:  bash scripts/apply-kwin-rule.sh
#       SIDEBAR_WIDTH=400 bash scripts/apply-kwin-rule.sh
# ──────────────────────────────────────────────────────────────
set -euo pipefail

# ID fixo — re-executar sobrescreve (sem duplicatas)
RULE_ID="{f7e8d9c0-b1a2-4334-95e6-f7a8b9c0d1e2}"
SIDEBAR_WIDTH="${SIDEBAR_WIDTH:-360}"
CONFIG="kwinrulesrc"

# ── Detecta resolução lógica via PySide6 ─────────────────────
# Usa o mesmo sistema de coordenadas que o KWin Rules espera.
# xrandr daria device pixels (errado quando há scaling).
read -r SCREEN_WIDTH SCREEN_HEIGHT <<< "$(python3 -c "
from PySide6.QtGui import QGuiApplication
import sys
app = QGuiApplication(sys.argv)
g = app.primaryScreen().geometry()
print(g.width(), g.height())
" 2>/dev/null)" || true

if [[ -z "${SCREEN_WIDTH:-}" || -z "${SCREEN_HEIGHT:-}" ]]; then
    echo "ERRO: Não foi possível detectar a resolução via PySide6."
    echo "Alternativa manual:"
    echo "  SCREEN_WIDTH=1920 SCREEN_HEIGHT=1080 bash $0"
    exit 1
fi

POS_X=$((SCREEN_WIDTH - SIDEBAR_WIDTH))

echo "── PEEK: Aplicando KWin Rule ──"
echo "   Tela (lógica): ${SCREEN_WIDTH}×${SCREEN_HEIGHT}"
echo "   Sidebar: ${SIDEBAR_WIDTH}×${SCREEN_HEIGHT} em (${POS_X}, 0)"
echo ""

# ── Escreve propriedades da regra ────────────────────────────
# Match: wmclass = app_id definido em main.py via setDesktopFileName("peek")
kwriteconfig6 --file "$CONFIG" --group "$RULE_ID" --key Description    "PEEK Sidebar"
kwriteconfig6 --file "$CONFIG" --group "$RULE_ID" --key wmclass        "peek"
kwriteconfig6 --file "$CONFIG" --group "$RULE_ID" --key wmclassmatch   "1"
kwriteconfig6 --file "$CONFIG" --group "$RULE_ID" --key wmclasscomplete "false"

# Posição — Force (SetRule::Force = 4)
# Canto superior direito, alinhado com a borda da tela
kwriteconfig6 --file "$CONFIG" --group "$RULE_ID" --key position      "${POS_X},0"
kwriteconfig6 --file "$CONFIG" --group "$RULE_ID" --key positionrule   "4"

# Tamanho — Force (SetRule::Force = 4)
kwriteconfig6 --file "$CONFIG" --group "$RULE_ID" --key size           "${SIDEBAR_WIDTH},${SCREEN_HEIGHT}"
kwriteconfig6 --file "$CONFIG" --group "$RULE_ID" --key sizerule       "4"

# Keep Above — Force (ForceRule::Force = 1)
kwriteconfig6 --file "$CONFIG" --group "$RULE_ID" --key above          "true"
kwriteconfig6 --file "$CONFIG" --group "$RULE_ID" --key aboverule      "1"

# No Titlebar / Frameless — Force (ForceRule::Force = 1)
kwriteconfig6 --file "$CONFIG" --group "$RULE_ID" --key noborder       "true"
kwriteconfig6 --file "$CONFIG" --group "$RULE_ID" --key noborderrule   "1"

# Skip Taskbar — Force (ForceRule::Force = 1)
kwriteconfig6 --file "$CONFIG" --group "$RULE_ID" --key skiptaskbar    "true"
kwriteconfig6 --file "$CONFIG" --group "$RULE_ID" --key skiptaskbarrule "1"

# Skip Pager — Force (ForceRule::Force = 1)
kwriteconfig6 --file "$CONFIG" --group "$RULE_ID" --key skippager      "true"
kwriteconfig6 --file "$CONFIG" --group "$RULE_ID" --key skippagerrule  "1"

# ── Registra a regra na seção [General] ──────────────────────
CURRENT_RULES=$(kreadconfig6 --file "$CONFIG" --group General --key rules 2>/dev/null || echo "")

if [[ "$CURRENT_RULES" != *"$RULE_ID"* ]]; then
    if [[ -z "$CURRENT_RULES" ]]; then
        NEW_RULES="$RULE_ID"
    else
        NEW_RULES="${CURRENT_RULES},${RULE_ID}"
    fi
    kwriteconfig6 --file "$CONFIG" --group General --key rules "$NEW_RULES"

    CURRENT_COUNT=$(kreadconfig6 --file "$CONFIG" --group General --key count 2>/dev/null || echo "0")
    kwriteconfig6 --file "$CONFIG" --group General --key count "$((CURRENT_COUNT + 1))"
    echo "   Regra adicionada à lista (count=$((CURRENT_COUNT + 1)))."
else
    echo "   Regra já registrada (sobrescrita com novos valores)."
fi

# ── Recarrega KWin ───────────────────────────────────────────
if command -v qdbus6 &>/dev/null; then
    qdbus6 org.kde.KWin /KWin reconfigure
elif command -v qdbus &>/dev/null; then
    qdbus org.kde.KWin /KWin reconfigure
elif command -v dbus-send &>/dev/null; then
    dbus-send --type=method_call --dest=org.kde.KWin /KWin org.kde.KWin.reconfigure
else
    echo "   AVISO: Não foi possível recarregar o KWin."
    echo "   Execute manualmente: qdbus org.kde.KWin /KWin reconfigure"
fi

echo ""
echo "✓ KWin Rule aplicada para 'peek'."
echo "  Reinicie o PEEK (python3 main.py) para testar."
echo ""
echo "NOTA: Se mudar a resolução ou scaling do monitor, re-execute este script."
echo "Para remover: System Settings → Window Management → Window Rules → 'PEEK Sidebar'"
