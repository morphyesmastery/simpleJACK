# Convert morphyes-logo.jpg (same folder) to PNG for the app icon.
# Usage: place morphyes-logo.jpg beside this script, then run: powershell -File convert-logo.ps1
Add-Type -AssemblyName System.Drawing
$src = Join-Path $PSScriptRoot 'morphyes-logo.jpg'
$dst = Join-Path $PSScriptRoot 'morphyes-logo.png'
$img = [System.Drawing.Image]::FromFile($src)
$img.Save($dst, [System.Drawing.Imaging.ImageFormat]::Png)
$img.Dispose()
Write-Host "Saved $dst"
