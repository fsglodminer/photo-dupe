#!/usr/bin/env bash
#
# Remove Photo Dupe. Your photos are never touched; this only removes the
# application, and optionally the index it built.
#
set -euo pipefail

APP_ID="photo-dupe"
PREFIX="${HOME}/.local"

KEEP_DATA=1
[[ "${1:-}" == "--purge" ]] && KEEP_DATA=0

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }

rm -rf "${PREFIX}/share/${APP_ID}"
rm -f  "${PREFIX}/bin/${APP_ID}"
rm -f  "${PREFIX}/share/applications/${APP_ID}.desktop"
rm -f  "${PREFIX}/share/icons/hicolor/scalable/apps/${APP_ID}.svg"
say "Removed the application"

if [[ "${KEEP_DATA}" -eq 0 ]]; then
    rm -rf "${XDG_CONFIG_HOME:-$HOME/.config}/${APP_ID}"
    rm -rf "${XDG_DATA_HOME:-$HOME/.local/share}/${APP_ID}"
    rm -rf "${XDG_CACHE_HOME:-$HOME/.cache}/${APP_ID}"
    say "Removed settings, the photo index and the thumbnail cache"
else
    say "Kept your settings and photo index (use --purge to delete them too)"
fi

command -v update-desktop-database >/dev/null 2>&1 && \
    update-desktop-database "${PREFIX}/share/applications" >/dev/null 2>&1 || true
say "Done. Your photo files were not touched."
