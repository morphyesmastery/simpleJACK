Add-Type -AssemblyName System.Drawing
$src = 'C:\Users\daddy\Desktop\FreedomNow Townhall\.next\standalone\public\morphyes-logo.jpg'
$dst = 'C:\Users\daddy\Desktop\simplejack-tauri-COPY\portable\SIMPLEJACK\morphyes-logo.png'
$img = [System.Drawing.Image]::FromFile($src)
$img.Save($dst, [System.Drawing.Imaging.ImageFormat]::Png)
$img.Dispose()
Write-Output "Converted to $dst"
