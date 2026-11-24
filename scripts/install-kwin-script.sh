#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# Instala e ativa o KWin Script "kslide-edge-trigger" no KDE Plasma 6.
# Uso:  bash scripts/install-kwin-script.sh
# ──────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_ID="kslide-edge-trigger"
SCRIPT_DIR="$(cd "$(dirname "$0")/../kwin-script/${SCRIPT_ID}" && pwd)"
INSTALL_DIR="${HOME}/.local/share/kwin/scripts/${SCRIPT_ID}"

echo "── PEEK: Instalando KWin Script ──"
echo "   Origem:  ${SCRIPT_DIR}"
echo "   Destino: ${INSTALL_DIR}"

# Copia os arquivos do script
mkdir -p "${INSTALL_DIR}/contents/code" "${INSTALL_DIR}/contents/config"
cp "${SCRIPT_DIR}/metadata.json"          "${INSTALL_DIR}/"
cp "${SCRIPT_DIR}/contents/code/main.js"  "${INSTALL_DIR}/contents/code/"
cp "${SCRIPT_DIR}/contents/config/main.xml" "${INSTALL_DIR}/contents/config/"

# Descarrega instância anterior no KWin (se ativa) antes de reinstalar
if command -v busctl &>/dev/null; then
    busctl --user call org.kde.KWin /Scripting org.kde.kwin.Scripting unloadScript s "${SCRIPT_ID}" &>/dev/null || true
fi

# Ativa o plugin no kwinrc
kwriteconfig6 --file kwinrc --group Plugins --key "${SCRIPT_ID}Enabled" true

# Recarrega o KWin para aplicar
if command -v busctl &>/dev/null; then
    busctl --user call org.kde.KWin /KWin org.kde.KWin reconfigure
elif command -v qdbus-qt6 &>/dev/null; then
    qdbus-qt6 org.kde.KWin /KWin reconfigure
elif command -v qdbus6 &>/dev/null; then
    qdbus6 org.kde.KWin /KWin reconfigure
elif command -v dbus-send &>/dev/null; then
    dbus-send --type=method_call --dest=org.kde.KWin /KWin org.kde.KWin.reconfigure
else
    echo "   AVISO: Não foi possível recarregar o KWin automaticamente."
    echo "   Faça logout/login ou execute: busctl --user call org.kde.KWin /KWin org.kde.KWin reconfigure"
fi

echo ""
echo "✓ KWin Script '${SCRIPT_ID}' instalado e ativado."
echo "  Teste: mova o mouse até a borda direita da tela."
echo "  (O app Python precisa estar rodando: python3 main.py)"
