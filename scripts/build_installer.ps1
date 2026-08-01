[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RuntimeDirectory,
    [Parameter(Mandatory = $true)]
    [string]$ChiakiDirectory,
    [Parameter(Mandatory = $true)]
    [string]$ViGEmBusInstaller,
    [Parameter(Mandatory = $true)]
    [string]$OutputPath,
    [string]$StageDirectory = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RuntimeDirectory = (Resolve-Path -LiteralPath $RuntimeDirectory).Path
$ChiakiDirectory = (Resolve-Path -LiteralPath $ChiakiDirectory).Path
$ViGEmBusInstaller = (Resolve-Path -LiteralPath $ViGEmBusInstaller).Path
$OutputPath = [System.IO.Path]::GetFullPath($OutputPath)

$Stage = if ($StageDirectory) {
    [System.IO.Path]::GetFullPath($StageDirectory)
} else {
    Join-Path $ProjectRoot "installer-stage"
}
if (Test-Path -LiteralPath $Stage) {
    throw "Installer stage already exists: $Stage"
}
if (Test-Path -LiteralPath $OutputPath) {
    throw "Installer output already exists: $OutputPath"
}

New-Item -ItemType Directory -Path $Stage | Out-Null
$AppRoot = Join-Path $Stage "GBFR"
& robocopy $RuntimeDirectory $AppRoot /E /XD logs | Out-Null
if ($LASTEXITCODE -gt 7) { throw "Failed to copy the application runtime." }
Move-Item -LiteralPath (Join-Path $AppRoot "GBFR_AutoReBattle.exe") -Destination (Join-Path $AppRoot "GBFR.exe")
Copy-Item -LiteralPath $ChiakiDirectory -Destination (Join-Path $AppRoot "Chiaki") -Recurse
Copy-Item -LiteralPath (Join-Path $ProjectRoot "启动工具_便携短路径.cmd") -Destination (Join-Path $AppRoot "启动工具.cmd")
Copy-Item -LiteralPath (Join-Path $ProjectRoot "README_使用说明.md") -Destination (Join-Path $AppRoot "README_使用说明.md")
Copy-Item -LiteralPath (Join-Path $ProjectRoot "RELEASE_NOTICES.md") -Destination (Join-Path $AppRoot "RELEASE_NOTICES.md")

$Payload = Join-Path $Stage "GBFR-payload.zip"
Compress-Archive -LiteralPath $AppRoot -DestinationPath $Payload -CompressionLevel Optimal
Copy-Item -LiteralPath (Join-Path $ProjectRoot "installer\install.cmd") -Destination (Join-Path $Stage "install.cmd")
Copy-Item -LiteralPath (Join-Path $ProjectRoot "installer\install.ps1") -Destination (Join-Path $Stage "install.ps1")
Copy-Item -LiteralPath (Join-Path $ProjectRoot "installer\ViGEmBus-LICENSE.txt") -Destination (Join-Path $Stage "ViGEmBus-LICENSE.txt")
Copy-Item -LiteralPath $ViGEmBusInstaller -Destination (Join-Path $Stage "ViGEmBus_1.22.0_x64_x86_arm64.exe")

$SedPath = Join-Path $Stage "installer.sed"
$Sed = @"
[Version]
Class=IEXPRESS
SEDVersion=3
[Options]
PackagePurpose=InstallApp
ShowInstallProgramWindow=1
HideExtractAnimation=0
UseLongFileName=1
InsideCompressed=1
CAB_FixedSize=0
CAB_ResvCodeSigning=0
RebootMode=N
InstallPrompt=
DisplayLicense=
FinishMessage=
TargetName=$OutputPath
FriendlyName=GBFR Chiaki 自动重战安装器
AppLaunched=install.cmd
PostInstallCmd=<None>
AdminQuietInstCmd=
UserQuietInstCmd=
SourceFiles=SourceFiles
[Strings]
FILE0=install.cmd
FILE1=install.ps1
FILE2=GBFR-payload.zip
FILE3=ViGEmBus_1.22.0_x64_x86_arm64.exe
FILE4=ViGEmBus-LICENSE.txt
[SourceFiles]
SourceFiles0=$Stage\
[SourceFiles0]
%FILE0%=
%FILE1%=
%FILE2%=
%FILE3%=
%FILE4%=
"@
Set-Content -LiteralPath $SedPath -Value $Sed -Encoding ascii

& iexpress.exe /N $SedPath
if ($LASTEXITCODE -ne 0) { throw "IExpress could not create the installer." }
if (-not (Test-Path -LiteralPath $OutputPath)) { throw "Installer output was not created." }

Write-Host "Installer created: $OutputPath"
