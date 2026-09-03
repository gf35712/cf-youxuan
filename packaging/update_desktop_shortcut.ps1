[CmdletBinding()]
param(
    [string]$ShortcutPath
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$distDirectory = Join-Path $projectRoot 'dist'
$iconPath = Join-Path $projectRoot 'packaging\assets\icons\app_icon_v7.ico'
$workingDirectory = $projectRoot
$guiExeName = 'CF' + [char]0x4F18 + [char]0x9009 + [char]0x6D4B + [char]0x901F + [char]0x5DE5 + [char]0x5177 + '.exe'
$targetPath = Join-Path $distDirectory $guiExeName

if ([string]::IsNullOrWhiteSpace($ShortcutPath)) {
    $shortcutName = 'CF' + [char]0x4F18 + [char]0x9009 + [char]0x6D4B + [char]0x901F + [char]0x5DE5 + [char]0x5177 + '.lnk'
    $ShortcutPath = Join-Path (Join-Path $env:USERPROFILE 'Desktop') $shortcutName
}

foreach ($requiredPath in @($targetPath, $iconPath, $workingDirectory)) {
    if ([string]::IsNullOrWhiteSpace($requiredPath) -or -not (Test-Path -LiteralPath $requiredPath)) {
        throw "Missing shortcut dependency: $requiredPath"
    }
}

$shortcutDirectory = Split-Path -Parent $ShortcutPath
if (-not (Test-Path -LiteralPath $shortcutDirectory)) {
    New-Item -ItemType Directory -Path $shortcutDirectory -Force | Out-Null
}

$wsh = New-Object -ComObject WScript.Shell
$shortcut = $wsh.CreateShortcut($ShortcutPath)
$shortcut.TargetPath = $targetPath
$shortcut.WorkingDirectory = $workingDirectory
$shortcut.IconLocation = "$iconPath,0"
$shortcut.Description = 'CF Youxuan Cloudflare node speed test tool'
$shortcut.Save()

Write-Host "Shortcut updated: $ShortcutPath"
Write-Host "Target: $targetPath"
Write-Host "Icon: $iconPath"

