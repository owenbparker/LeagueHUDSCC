# Installs League HUD to C:\LeagueHUD\LeagueHUD and adds desktop shortcuts.
# Run it with install.bat. Safe to run again to update: your settings
# (config.json), uploaded logos and output files are kept.

$ErrorActionPreference = 'Stop'
$Source = Split-Path -Parent $MyInvocation.MyCommand.Path
$Target = 'C:\LeagueHUD\LeagueHUD'

Write-Host ''
Write-Host "Installing League HUD to $Target" -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $Target | Out-Null

if ((Resolve-Path $Source).Path.TrimEnd('\') -ne $Target) {
    robocopy $Source $Target /E /XD .git output __pycache__ /XF config.json /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Copying files failed (robocopy exit code $LASTEXITCODE)" }
}
# Files from a downloaded zip are marked as "from the internet"; clear that
# so Windows doesn't block the start scripts.
Get-ChildItem -Path $Target -Recurse -File | Unblock-File
Write-Host '  Files copied.' -ForegroundColor Green

# Python 3.9+ is the only thing the HUD needs.
function Test-Python {
    foreach ($cmd in @(@('py', '-3'), @('python'))) {
        try {
            $exe = $cmd[0]
            $rest = @($cmd | Select-Object -Skip 1)
            $out = & $exe @rest -c "import sys; print(sys.version_info >= (3, 9))" 2>$null
            if ($out -eq 'True') { return $true }
        } catch { }
    }
    return $false
}

if (Test-Python) {
    Write-Host '  Python found.' -ForegroundColor Green
} else {
    Write-Host '  Python 3.9 or newer was not found.' -ForegroundColor Yellow
    $answer = Read-Host '  Install Python 3.12 now with winget? (Y/N)'
    if ($answer -match '^[Yy]') {
        winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
        Write-Host '  Python installed. If the HUD says Python is missing, sign out and back in (or restart) once.' -ForegroundColor Green
    } else {
        Write-Host '  Install Python from https://www.python.org/downloads/ (tick "Add Python to PATH").' -ForegroundColor Yellow
    }
}

# Desktop shortcuts.
$desktop = [Environment]::GetFolderPath('Desktop')
$shell = New-Object -ComObject WScript.Shell
foreach ($s in @(@('League HUD', 'start-hud.bat'), @('League HUD (Demo)', 'start-demo.bat'))) {
    $link = $shell.CreateShortcut((Join-Path $desktop ($s[0] + '.lnk')))
    $link.TargetPath = Join-Path $Target $s[1]
    $link.WorkingDirectory = $Target
    $link.Save()
}
Write-Host '  Desktop shortcuts created: "League HUD" and "League HUD (Demo)".' -ForegroundColor Green

Write-Host ''
Write-Host 'Done. Start it from the desktop shortcut, then open http://localhost:8777/control' -ForegroundColor Cyan
Write-Host "vMix CSV file: $Target\output\hud.csv"
