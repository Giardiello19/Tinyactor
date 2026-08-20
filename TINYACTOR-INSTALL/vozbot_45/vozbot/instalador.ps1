# =====================================================================
#  instalador.ps1 — Instalador de vozbot con ventana gráfica.
#  No requiere nada previo: WinForms viene con Windows.
#  Se lanza desde INSTALAR.bat
# =====================================================================

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$ORIGEN  = Split-Path -Parent $MyInvocation.MyCommand.Path
$DESTINO = Join-Path $env:LOCALAPPDATA "vozbot"

# ---------------------------------------------------------------- UI
$f = New-Object System.Windows.Forms.Form
$f.Text = "Instalar vozbot"
$f.Size = New-Object System.Drawing.Size(620, 520)
$f.StartPosition = "CenterScreen"
$f.FormBorderStyle = "FixedSingle"
$f.MaximizeBox = $false
$f.BackColor = [System.Drawing.Color]::FromArgb(18, 20, 26)

$titulo = New-Object System.Windows.Forms.Label
$titulo.Text = "vozbot"
$titulo.Font = New-Object System.Drawing.Font("Segoe UI", 22, [System.Drawing.FontStyle]::Bold)
$titulo.ForeColor = [System.Drawing.Color]::FromArgb(240, 164, 65)
$titulo.Location = New-Object System.Drawing.Point(28, 22)
$titulo.Size = New-Object System.Drawing.Size(400, 42)
$f.Controls.Add($titulo)

$sub = New-Object System.Windows.Forms.Label
$sub.Text = "Automatizacion de lectura con voz sintetica"
$sub.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$sub.ForeColor = [System.Drawing.Color]::FromArgb(125, 132, 150)
$sub.Location = New-Object System.Drawing.Point(30, 62)
$sub.Size = New-Object System.Drawing.Size(500, 22)
$f.Controls.Add($sub)

# --- lista de pasos ---
$pasos = @(
    "Comprobar Python",
    "Copiar los archivos",
    "Instalar las librerias",
    "Descargar el navegador",
    "Instalar el cable de audio",
    "Crear accesos directos"
)
$etiquetas = @()
$y = 100
foreach ($p in $pasos) {
    $l = New-Object System.Windows.Forms.Label
    $l.Text = "   $p"
    $l.Font = New-Object System.Drawing.Font("Segoe UI", 10)
    $l.ForeColor = [System.Drawing.Color]::FromArgb(125, 132, 150)
    $l.Location = New-Object System.Drawing.Point(34, $y)
    $l.Size = New-Object System.Drawing.Size(420, 24)
    $f.Controls.Add($l)
    $etiquetas += $l
    $y += 27
}

$barra = New-Object System.Windows.Forms.ProgressBar
$barra.Location = New-Object System.Drawing.Point(30, 275)
$barra.Size = New-Object System.Drawing.Size(552, 16)
$barra.Maximum = $pasos.Count
$f.Controls.Add($barra)

$registro = New-Object System.Windows.Forms.TextBox
$registro.Multiline = $true
$registro.ScrollBars = "Vertical"
$registro.ReadOnly = $true
$registro.BackColor = [System.Drawing.Color]::FromArgb(11, 13, 18)
$registro.ForeColor = [System.Drawing.Color]::FromArgb(150, 200, 175)
$registro.Font = New-Object System.Drawing.Font("Consolas", 8.5)
$registro.Location = New-Object System.Drawing.Point(30, 302)
$registro.Size = New-Object System.Drawing.Size(552, 118)
$f.Controls.Add($registro)

$estado = New-Object System.Windows.Forms.Label
$estado.Text = "Listo para instalar"
$estado.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$estado.ForeColor = [System.Drawing.Color]::FromArgb(125, 132, 150)
$estado.Location = New-Object System.Drawing.Point(30, 432)
$estado.Size = New-Object System.Drawing.Size(360, 22)
$f.Controls.Add($estado)

$boton = New-Object System.Windows.Forms.Button
$boton.Text = "Instalar"
$boton.Font = New-Object System.Drawing.Font("Segoe UI", 11, [System.Drawing.FontStyle]::Bold)
$boton.Size = New-Object System.Drawing.Size(160, 40)
$boton.Location = New-Object System.Drawing.Point(422, 428)
$boton.FlatStyle = "Flat"
$boton.BackColor = [System.Drawing.Color]::FromArgb(240, 164, 65)
$boton.ForeColor = [System.Drawing.Color]::FromArgb(18, 20, 26)
$boton.FlatAppearance.BorderSize = 0
$f.Controls.Add($boton)

# ------------------------------------------------------------ utilidades
function Escribir([string]$texto) {
    $registro.AppendText("$texto`r`n")
    $registro.SelectionStart = $registro.Text.Length
    $registro.ScrollToCaret()
    [System.Windows.Forms.Application]::DoEvents()
}

function Paso([int]$i, [string]$simbolo, [System.Drawing.Color]$color) {
    $etiquetas[$i].Text = "$simbolo $($pasos[$i])"
    $etiquetas[$i].ForeColor = $color
    [System.Windows.Forms.Application]::DoEvents()
}

$verde  = [System.Drawing.Color]::FromArgb(94, 234, 212)
$activo = [System.Drawing.Color]::FromArgb(240, 164, 65)
$rojo   = [System.Drawing.Color]::FromArgb(255, 130, 120)

function BuscarPython() {
    foreach ($c in @("py", "python", "python3")) {
        try {
            $v = & $c --version 2>&1
            if ($LASTEXITCODE -eq 0 -and "$v" -match "Python 3\.(\d+)") {
                if ([int]$Matches[1] -ge 10) { return $c }
            }
        } catch { }
    }
    return $null
}

# ------------------------------------------------------------ instalación
# Un unico manejador con estado: Add_Click ACUMULA manejadores, no los
# reemplaza, asi que anadir otro haria reinstalar al pulsar de nuevo.
$script:fase = "instalar"

$boton.Add_Click({
    if ($script:fase -eq "abrir") {
        Start-Process (Join-Path $DESTINO "VOZBOT.bat")
        $f.Close()
        return
    }
    if ($script:fase -eq "cerrar") { $f.Close(); return }
    if ($script:fase -ne "instalar") { return }
    $script:fase = "trabajando"

    $boton.Enabled = $false
    $boton.Text = "Instalando..."

    # --- 1. Python -----------------------------------------------------
    Paso 0 ">" $activo
    $estado.Text = "Comprobando Python..."
    $py = BuscarPython
    if (-not $py) {
        Escribir "No hay Python 3.10 o superior. Intentando instalarlo..."
        try {
            winget install -e --id Python.Python.3.12 --silent --accept-source-agreements --accept-package-agreements | Out-Null
            $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                        [System.Environment]::GetEnvironmentVariable("Path", "User")
            $py = BuscarPython
        } catch { }
    }
    if (-not $py) {
        Paso 0 "x" $rojo
        Escribir ""
        Escribir "No pude instalar Python automaticamente."
        Escribir "Instalalo desde python.org/downloads (marca 'Add to PATH')"
        Escribir "y vuelve a ejecutar este instalador."
        $estado.Text = "Falta Python"
        $boton.Text = "Cerrar"
        $script:fase = "cerrar"
        $boton.Enabled = $true
        return
    }
    Escribir "Python encontrado: $py"
    Paso 0 "v" $verde
    $barra.Value = 1

    # --- 2. archivos ---------------------------------------------------
    Paso 1 ">" $activo
    $estado.Text = "Copiando archivos..."
    try {
        New-Item -ItemType Directory -Force -Path $DESTINO | Out-Null
        Copy-Item (Join-Path $ORIGEN "run.py")          $DESTINO -Force
        Copy-Item (Join-Path $ORIGEN "diagnostico.py")  $DESTINO -Force -ErrorAction SilentlyContinue
        Copy-Item (Join-Path $ORIGEN "probar_audio.py") $DESTINO -Force -ErrorAction SilentlyContinue
        Copy-Item (Join-Path $ORIGEN "requirements.txt") $DESTINO -Force
        $paquete = Join-Path $DESTINO "vozbot"
        New-Item -ItemType Directory -Force -Path $paquete | Out-Null
        Copy-Item (Join-Path $ORIGEN "vozbot\*.py") $paquete -Force

        # la configuracion solo si no existe: no pisar la del usuario
        $cfg = Join-Path $DESTINO "config.yaml"
        if (-not (Test-Path $cfg)) {
            Copy-Item (Join-Path $ORIGEN "config.yaml") $cfg -Force
            Escribir "Configuracion inicial creada"
        } else {
            Escribir "Se conserva tu config.yaml"
        }
        Escribir "Archivos en $DESTINO"
        Paso 1 "v" $verde
    } catch {
        Paso 1 "x" $rojo
        Escribir "Error copiando: $_"
    }
    $barra.Value = 2

    # --- 3. librerias --------------------------------------------------
    Paso 2 ">" $activo
    $estado.Text = "Instalando librerias (tarda unos minutos)..."
    Escribir "Instalando dependencias..."
    try {
        $req = Join-Path $DESTINO "requirements.txt"
        $salida = & $py -m pip install --user -r $req 2>&1
        if ($LASTEXITCODE -eq 0) {
            Escribir "Librerias instaladas"
            Paso 2 "v" $verde
        } else {
            Paso 2 "x" $rojo
            Escribir ($salida | Select-Object -Last 6)
        }
    } catch {
        Paso 2 "x" $rojo
        Escribir "Error: $_"
    }
    $barra.Value = 3

    # --- 4. navegador --------------------------------------------------
    Paso 3 ">" $activo
    $estado.Text = "Descargando el navegador de automatizacion..."
    try {
        & $py -m playwright install chromium 2>&1 | Out-Null
        Escribir "Navegador listo"
        Paso 3 "v" $verde
    } catch {
        Paso 3 "x" $rojo
        Escribir "No pude descargar el navegador: $_"
    }
    $barra.Value = 4

    # --- 5. cable de audio ---------------------------------------------
    Paso 4 ">" $activo
    $estado.Text = "Comprobando el cable de audio..."
    $tieneCable = $false
    try {
        $tieneCable = [bool](Get-CimInstance Win32_SoundDevice -ErrorAction SilentlyContinue |
                             Where-Object { $_.Name -like "*CABLE*" -or $_.Name -like "*VB-Audio*" })
    } catch { }

    if ($tieneCable) {
        Escribir "VB-CABLE ya esta instalado"
        Paso 4 "v" $verde
    } else {
        Escribir "Falta VB-CABLE (el microfono virtual)."
        $r = [System.Windows.Forms.MessageBox]::Show(
            "vozbot necesita VB-CABLE para entregar la voz al navegador.`n`n" +
            "Se abrira la pagina de descarga. Descomprime el archivo y ejecuta`n" +
            "VBCABLE_Setup_x64.exe COMO ADMINISTRADOR. Despues reinicia el equipo.`n`n" +
            "Abrir la pagina ahora?",
            "Falta el cable de audio",
            [System.Windows.Forms.MessageBoxButtons]::YesNo,
            [System.Windows.Forms.MessageBoxIcon]::Information)
        if ($r -eq "Yes") { Start-Process "https://vb-audio.com/Cable/" }
        Escribir "Instala VB-CABLE y reinicia antes de usar vozbot"
        Paso 4 "!" $activo
    }
    $barra.Value = 5

    # --- 6. accesos directos -------------------------------------------
    Paso 5 ">" $activo
    $estado.Text = "Creando accesos directos..."
    try {
        $lanzador = Join-Path $DESTINO "VOZBOT.bat"
@"
@echo off
cd /d "%~dp0"
title vozbot
$py run.py
if errorlevel 1 pause
"@ | Set-Content -Path $lanzador -Encoding ASCII

        $shell = New-Object -ComObject WScript.Shell
        foreach ($carpeta in @([Environment]::GetFolderPath("Desktop"),
                               (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"))) {
            $acceso = $shell.CreateShortcut((Join-Path $carpeta "vozbot.lnk"))
            $acceso.TargetPath = $lanzador
            $acceso.WorkingDirectory = $DESTINO
            $acceso.Description = "Automatizacion de lectura con voz sintetica"
            $acceso.Save()
        }
        Escribir "Acceso directo creado en el escritorio"
        Paso 5 "v" $verde
    } catch {
        Paso 5 "x" $rojo
        Escribir "No pude crear los accesos: $_"
    }
    $barra.Value = 6

    # --- fin -----------------------------------------------------------
    Escribir ""
    Escribir "== INSTALACION TERMINADA =="
    Escribir ""
    Escribir "Antes de jugar:"
    Escribir "  1. Arranca tu app de voz (python app.py)"
    Escribir "  2. Abre Chrome con:"
    Escribir "     chrome.exe --remote-debugging-port=9222 --user-data-dir=C:\chromebot"
    Escribir "  3. Doble clic en el acceso 'vozbot' del escritorio"
    $estado.Text = "Instalacion terminada"
    $boton.Text = "Abrir vozbot"
    $boton.BackColor = $verde
    $script:fase = "abrir"
    $boton.Enabled = $true
})

[void]$f.ShowDialog()
