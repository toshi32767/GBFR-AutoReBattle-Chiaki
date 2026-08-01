[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ForbiddenNames = @(
    "logs", "screenshot", "Chiaki", "build", "dist", "release",
    "__pycache__", "nuitka-crash-report.xml"
)
$ForbiddenExtensions = @(".exe", ".dll", ".pdb", ".log")

$Findings = Get-ChildItem -LiteralPath $ProjectRoot -Force -Recurse | Where-Object {
    $_.FullName -notmatch '[\\/]\.git([\\/]|$)' -and (
        $ForbiddenNames -contains $_.Name -or
        ($_.PSIsContainer -eq $false -and $ForbiddenExtensions -contains $_.Extension.ToLowerInvariant())
    )
}

if ($Findings) {
    Write-Host "Publish-blocking files/directories found:" -ForegroundColor Red
    $Findings | ForEach-Object { Write-Host "  $($_.FullName)" }
    exit 1
}

Write-Host "Publish tree check passed. Confirm upstream license permission before making the repository public." -ForegroundColor Green
