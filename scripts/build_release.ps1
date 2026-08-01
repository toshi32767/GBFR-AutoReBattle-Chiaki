[CmdletBinding()]
param(
    [string]$Python = "py -3.10"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $ProjectRoot

$PythonCommand = $Python -split " "
& $PythonCommand[0] $PythonCommand[1..($PythonCommand.Count - 1)] -m pip install -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }

& $PythonCommand[0] $PythonCommand[1..($PythonCommand.Count - 1)] -m PyInstaller --noconfirm --clean --distpath release\dist --workpath release\build build-pyinstaller\GBFR_AutoReBattle.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

Write-Host "Build completed: $ProjectRoot\release\dist\GBFR_AutoReBattle"
