<#
.SYNOPSIS
    Builds ClaudeUsageWidget.exe with PyInstaller.

.EXAMPLE
    .\build.ps1
    .\build.ps1 -Install      # also add it to the Startup folder
#>
[CmdletBinding()]
param(
    [switch]$Install,
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

# Trim Qt modules the widget never touches; this roughly halves the bundle.
$excludes = @(
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtQuick3D",
    "PySide6.QtNetwork", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtPdf",
    "PySide6.QtPdfWidgets", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
    "PySide6.QtPrintSupport", "PySide6.QtDBus", "PySide6.QtHelp", "PySide6.QtDesigner",
    "PySide6.QtCharts", "PySide6.QtMultimedia", "PySide6.QtWebEngineCore",
    "tkinter", "unittest", "pydoc"
    # Note: do not exclude http/email/xml — urllib.request needs them.
) | ForEach-Object { "--exclude-module", $_ }

Write-Host "Building executable..." -ForegroundColor Cyan
& $venvPython -m PyInstaller `
    --noconfirm --clean --onefile --noconsole `
    --name "ClaudeUsageWidget" `
    --icon "$root\app.ico" `
    @excludes `
    "$root\claude_usage_widget\__main__.py"

$exe = Join-Path $root "dist\ClaudeUsageWidget.exe"
if (-not (Test-Path $exe)) { throw "Build failed: $exe not found." }

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
