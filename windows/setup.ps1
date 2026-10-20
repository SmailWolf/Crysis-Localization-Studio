$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Root = Split-Path -Parent $PSScriptRoot
$Manifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'artifacts.json') -Raw | ConvertFrom-Json
$Cache = Join-Path $Root '.windows-downloads'
$Destination = Join-Path $Root '.python'
$Stage = Join-Path $Root ('.python-stage-' + [Guid]::NewGuid().ToString('N'))
Add-Type -AssemblyName System.IO.Compression.FileSystem
New-Item -ItemType Directory -Force -Path $Cache | Out-Null
New-Item -ItemType Directory -Force -Path $Stage | Out-Null
function Get-VerifiedFile($Item) {
    $File = Join-Path $Cache $Item.filename
    $Valid = (Test-Path -LiteralPath $File) -and ((Get-Item -LiteralPath $File).Length -eq $Item.size)
    if ($Valid) { $Valid = ((Get-FileHash -LiteralPath $File -Algorithm SHA256).Hash.ToLowerInvariant() -eq $Item.sha256) }
    if (-not $Valid) {
        Write-Host ('Downloading ' + $Item.filename)
        $Part = $File + '.part'
        Invoke-WebRequest -Uri $Item.url -OutFile $Part -UseBasicParsing
        if ((Get-Item -LiteralPath $Part).Length -ne $Item.size) { throw 'Incomplete download. Run Start CryLocal.cmd again.' }
        if ((Get-FileHash -LiteralPath $Part -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Item.sha256) { throw 'Download checksum mismatch.' }
        Move-Item -LiteralPath $Part -Destination $File -Force
    }
    return $File
}
try {
    $PythonZip = Get-VerifiedFile $Manifest.python
    [IO.Compression.ZipFile]::ExtractToDirectory($PythonZip, $Stage)
    $Packages = Join-Path $Stage 'Lib\site-packages'
    New-Item -ItemType Directory -Force -Path $Packages | Out-Null
    foreach ($Name in @('shiboken6', 'PySide6_Essentials')) {
        $Wheel = Get-VerifiedFile $Manifest.$Name
        [IO.Compression.ZipFile]::ExtractToDirectory($Wheel, $Packages)
    }
    $SearchPath = "python314.zip`r`n.`r`nLib\site-packages`r`n..`r`nimport site`r`n"
    [IO.File]::WriteAllText((Join-Path $Stage 'python314._pth'), $SearchPath, ([System.Text.UTF8Encoding]::new($false)))
    & (Join-Path $Stage 'python.exe') -c "from PySide6.QtWidgets import QApplication; print('CryLocal environment ready')"
    if ($LASTEXITCODE -ne 0) { throw 'The Windows interface libraries could not be loaded.' }
    if (Test-Path -LiteralPath $Destination) { Remove-Item -LiteralPath $Destination -Recurse -Force }
    Move-Item -LiteralPath $Stage -Destination $Destination
    Write-Host 'CryLocal is ready.'
} finally {
    if (Test-Path -LiteralPath $Stage) { Remove-Item -LiteralPath $Stage -Recurse -Force }
}
