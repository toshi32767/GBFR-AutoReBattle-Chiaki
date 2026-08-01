[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ForbiddenSegments = @(
    "logs", "screenshot", "Chiaki", "build", "dist", "release",
    "__pycache__", "installer", "installer-stage"
)
$ForbiddenExtensions = @(".exe", ".dll", ".pdb", ".log")

$TrackedPaths = @(& git -C $ProjectRoot ls-files --cached --others --exclude-standard)
if ($LASTEXITCODE -ne 0) {
    throw "git ls-files failed with exit code $LASTEXITCODE"
}

$Findings = foreach ($RelativePath in $TrackedPaths) {
    $Normalized = $RelativePath -replace '\\', '/'
    $Segments = @($Normalized -split '/')
    $Extension = [System.IO.Path]::GetExtension($Normalized).ToLowerInvariant()
    $BaseName = [System.IO.Path]::GetFileName($Normalized)

    $HasForbiddenSegment = $false
    foreach ($Segment in $Segments) {
        foreach ($Forbidden in $ForbiddenSegments) {
            if ($Segment -eq $Forbidden -or $Segment -like "$Forbidden-v*") {
                $HasForbiddenSegment = $true
                break
            }
        }
        if ($HasForbiddenSegment) { break }
    }

    if (
        $HasForbiddenSegment -or
        $ForbiddenExtensions -contains $Extension -or
        $BaseName -eq "nuitka-crash-report.xml"
    ) {
        $RelativePath
    }
}

if ($Findings) {
    Write-Host "Publish-blocking tracked or unignored paths found:" -ForegroundColor Red
    $Findings | Sort-Object -Unique | ForEach-Object { Write-Host "  $_" }
    exit 1
}

Write-Host "Publish tree file check passed." -ForegroundColor Green
Write-Host "This does not clear the upstream-license and OCR-model blockers in PUBLISH_BLOCKERS.md." -ForegroundColor Yellow
