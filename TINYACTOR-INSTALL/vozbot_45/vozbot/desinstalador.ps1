# =====================================================================
#  desinstalador.ps1 — Quita vozbot del equipo.
#  No toca VB-CABLE ni Python: pueden estar en uso por otros programas.
# =====================================================================

Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.Application]::EnableVisualStyles()

$DESTINO = Join-Path $env:LOCALAPPDATA "vozbot"

$r = [System.Windows.Forms.MessageBox]::Show(
    "Se va a quitar vozbot de:`n$DESTINO`n`n" +
    "Tu config.yaml y la carpeta logs se guardaran en el Escritorio.`n`n" +
    "No se desinstalan Python ni VB-CABLE: otros programas pueden usarlos.`n`n" +
    "Continuar?",
    "Desinstalar vozbot",
    [System.Windows.Forms.MessageBoxButtons]::YesNo,
    [System.Windows.Forms.MessageBoxIcon]::Warning)

if ($r -ne "Yes") { exit }

# --- respaldo de lo que le importa al usuario ---
$respaldo = Join-Path ([Environment]::GetFolderPath("Desktop")) "vozbot-respaldo"
try {
    if (Test-Path (Join-Path $DESTINO "config.yaml")) {
        New-Item -ItemType Directory -Force -Path $respaldo | Out-Null
        Copy-Item (Join-Path $DESTINO "config.yaml") $respaldo -Force
    }
    if (Test-Path (Join-Path $DESTINO "logs")) {
        Copy-Item (Join-Path $DESTINO "logs") $respaldo -Recurse -Force -ErrorAction SilentlyContinue
    }
} catch { }

# --- accesos directos ---
foreach ($carpeta in @([Environment]::GetFolderPath("Desktop"),
                       (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"))) {
    Remove-Item (Join-Path $carpeta "vozbot.lnk") -Force -ErrorAction SilentlyContinue
}

# --- archivos ---
Remove-Item $DESTINO -Recurse -Force -ErrorAction SilentlyContinue

$mensaje = "vozbot se ha quitado."
if (Test-Path $respaldo) { $mensaje += "`n`nTu configuracion quedo en:`n$respaldo" }
[System.Windows.Forms.MessageBox]::Show($mensaje, "Listo",
    [System.Windows.Forms.MessageBoxButtons]::OK,
    [System.Windows.Forms.MessageBoxIcon]::Information)
