[CmdletBinding()]
param(
    [string]$Python = "py -3"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $ProjectRoot

$PythonCommand = @($Python -split " ")
if ($PythonCommand.Count -eq 0 -or [string]::IsNullOrWhiteSpace($PythonCommand[0])) {
    throw "Python command is empty."
}
$PythonExecutable = $PythonCommand[0]
$PythonArguments = if ($PythonCommand.Count -gt 1) { @($PythonCommand[1..($PythonCommand.Count - 1)]) } else { @() }

Write-Host "Installing GBFR AutoReBattle development/runtime dependencies with: $Python"
$check = @'
import importlib.util
required = {
    "windows-capture": "windows_capture",
    "vgamepad": "vgamepad",
    "onnxruntime": "onnxruntime",
    "Shapely": "shapely",
    "pyclipper": "pyclipper",
    "scikit-image": "skimage",
    "PyYAML": "yaml",
}
missing = [name for name, module in required.items() if importlib.util.find_spec(module) is None]
if missing:
    print("Missing runtime dependencies: " + ", ".join(missing))
    raise SystemExit(1)
print("All runtime dependencies are available.")
'@
$preflight = $check | & $PythonExecutable @PythonArguments - 2>&1
$preflight | ForEach-Object { Write-Host $_ }
if ($LASTEXITCODE -eq 0) {
    Write-Host "Development environment is already ready; skipping pip installation." -ForegroundColor Green
    return
}

Write-Host "Missing dependencies detected; installing requirements-dev.txt..."
& $PythonExecutable @PythonArguments -m pip install -r requirements-dev.txt
if ($LASTEXITCODE -ne 0) {
    throw "Dependency installation failed with exit code $LASTEXITCODE."
}

Write-Host "Checking complete runtime dependencies..."
$diagnostics = $check | & $PythonExecutable @PythonArguments - 2>&1
$diagnostics | ForEach-Object { Write-Host $_ }
if ($LASTEXITCODE -ne 0) {
    throw "Runtime dependency check failed with exit code $LASTEXITCODE."
}

Write-Host "Development environment is ready. Run: $Python .\main.py --diagnostics" -ForegroundColor Green
