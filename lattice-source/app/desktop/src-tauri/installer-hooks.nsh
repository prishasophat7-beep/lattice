; Installer hooks (Tauri NSIS). Builds before 2026-09-25 named the programs oasis-desktop.exe and
; oasis-backend.exe; an upgrade removes those leftovers so only the lattice-* programs remain.
!macro NSIS_HOOK_POSTINSTALL
  Delete "$INSTDIR\oasis-desktop.exe"
  Delete "$INSTDIR\runtime\backend\oasis-backend.exe"
!macroend
