# Build Flow.exe: a single-file Windows app with Python and all libraries packed inside.
#
#   .\build.ps1             # build dist\Flow.exe
#   .\build.ps1 -Install    # also install it to %LOCALAPPDATA%\Programs\Flow and restart Flow
param([switch]$Install)
Set-Location $PSScriptRoot

function Step($what, [scriptblock]$cmd) {
    # Check exit codes rather than using -ErrorAction Stop: Windows PowerShell 5.1 treats any text a
    # program writes to stderr (like pip's "new version available" notice) as an error.
    & $cmd
    if ($LASTEXITCODE -ne 0) { Write-Host "Failed: $what" -ForegroundColor Red; exit 1 }
}

$icon = Join-Path $PSScriptRoot "flow.ico"  # full path: PyInstaller resolves --icon from the build folder
Step "installing requirements" { py -m pip install --quiet --disable-pip-version-check -r requirements.txt pyinstaller }
Step "drawing the icon" { py tools\make_icon.py }
Step "building the exe" { py -m PyInstaller --noconfirm --onefile --noconsole --name Flow --icon $icon `
    --distpath dist --workpath build --specpath build --log-level WARN flow.pyw }
Write-Host "Built dist\Flow.exe" -ForegroundColor Green

if ($Install) {
    $dest = "$env:LOCALAPPDATA\Programs\Flow"
    New-Item -ItemType Directory -Force $dest | Out-Null
    # Close the running copy first; the exe is locked while it runs.
    Get-Process Flow -ErrorAction SilentlyContinue | ForEach-Object { $_.Kill(); $_.WaitForExit(5000) | Out-Null }
    Copy-Item dist\Flow.exe "$dest\Flow.exe" -Force
    $lnk = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Flow.lnk"
    $s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
    $s.TargetPath = "$dest\Flow.exe"; $s.WorkingDirectory = $dest; $s.Save()
    Start-Process "$dest\Flow.exe"
    Write-Host "Installed to $dest and started Flow" -ForegroundColor Green
}
