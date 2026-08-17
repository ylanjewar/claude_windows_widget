<#
.SYNOPSIS
    Builds ClaudeUsageWidget.exe with PyInstaller.

.EXAMPLE
    .\build.ps1
    .\build.ps1 -Install      # also add it to the Startup folder
    .\build.ps1 -OneDir       # unpacked folder build; less prone to AV flagging

.NOTES
    Antivirus software frequently flags PyInstaller onefile executables: the
    bootloader unpacks and executes at runtime, which is also what malware
    packers do. If Norton or Defender quarantines the build, -OneDir usually
    avoids it. You can also skip packaging entirely and run from source with
    `python -m claude_usage_widget`; the widget's own "Start with Windows"
    option then registers pythonw.exe, which no antivirus objects to.
#>
[CmdletBinding()]
param(
    [switch]$Install,
    [switch]$OneDir,
    [switch]$Trim,
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$venv = Join-Path $root ".venv"
if (-not (Test-Path $venv)) {
    Write-Host "Creating virtual environment..." -ForegroundColor Cyan
    & $Python -m venv $venv
}

$venvPython = Join-Path $venv "Scripts\python.exe"
Write-Host "Installing dependencies..." -ForegroundColor Cyan
& $venvPython -m pip install --upgrade pip --quiet
& $venvPython -m pip install -r requirements.txt pyinstaller --quiet

Write-Host "Generating icon..." -ForegroundColor Cyan
& $venvPython -m claude_usage_widget.icon "$root\app.ico"

# Trimming Qt modules shrinks the bundle but can drop a DLL that a module we do
# use depends on, producing an "ordinal could not be located" failure at launch.
# Correctness first: build everything unless -Trim is passed explicitly.
$excludes = @()
if ($Trim) {
    Write-Host "Trimming unused Qt modules (-Trim). If the build fails to" -ForegroundColor Yellow
    Write-Host "launch, rebuild without -Trim." -ForegroundColor Yellow
    $excludes = @(
        "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtQuick3D",
        "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
        "PySide6.QtCharts", "PySide6.QtMultimedia", "PySide6.QtWebEngineCore",
        "PySide6.QtDesigner", "PySide6.QtHelp",
        "tkinter", "unittest", "pydoc"
        # Do not exclude http/email/xml — urllib.request needs them. Do not
        # exclude QtNetwork, QtOpenGL, QtDBus or QtPrintSupport: QtGui and
        # QtWidgets link against them even though this code never imports them.
    ) | ForEach-Object { "--exclude-module", $_ }
}

$packaging = if ($OneDir) { "--onedir" } else { "--onefile" }
Write-Host "Building executable ($packaging)..." -ForegroundColor Cyan
& $venvPython -m PyInstaller `
    --noconfirm --clean $packaging --noconsole `
    --name "ClaudeUsageWidget" `
    --icon "$root\app.ico" `
    @excludes `
    "$root\claude_usage_widget\__main__.py"

$exe = if ($OneDir) {
    Join-Path $root "dist\ClaudeUsageWidget\ClaudeUsageWidget.exe"
} else {
    Join-Path $root "dist\ClaudeUsageWidget.exe"
}
if (-not (Test-Path $exe)) {
    Write-Host "Build failed: $exe not found." -ForegroundColor Red
    Write-Host "If the build looked successful, your antivirus may have quarantined" -ForegroundColor Yellow
    Write-Host "the output. Check its history, or retry with -OneDir. You can also" -ForegroundColor Yellow
    Write-Host "skip packaging: run 'python -m claude_usage_widget' and use the" -ForegroundColor Yellow
    Write-Host "widget's 'Start with Windows' menu option instead." -ForegroundColor Yellow
    throw "Executable not found after build."
}

$sizeMb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host "Built $exe ($sizeMb MB)" -ForegroundColor Green

if ($Install) {
    $startup = [Environment]::GetFolderPath("Startup")
    $link = Join-Path $startup "Claude Usage Widget.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($link)
    $shortcut.TargetPath = $exe
    $shortcut.WorkingDirectory = (Split-Path -Parent $exe)
    $shortcut.Description = "Claude usage widget"
    $shortcut.WindowStyle = 7
    $shortcut.Save()
    Write-Host "Added to Startup: $link" -ForegroundColor Green
}

Write-Host ""
Write-Host "Run it now with:  $exe"
