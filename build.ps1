# Empaqueta la carpeta addon/ en dist/teamsChat-<versión>.nvda-addon
$ErrorActionPreference = 'Stop'

$src = Join-Path $PSScriptRoot 'addon'
$manifest = Get-Content (Join-Path $src 'manifest.ini') -Encoding UTF8
$version = ($manifest | Select-String '^version\s*=\s*(.+)$').Matches[0].Groups[1].Value.Trim()

$dist = Join-Path $PSScriptRoot 'dist'
New-Item -ItemType Directory -Force $dist | Out-Null
$out = Join-Path $dist "teamsChat-$version.nvda-addon"
if (Test-Path $out) { Remove-Item $out }

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::Open($out, 'Create')
try {
	Get-ChildItem $src -Recurse -File |
		Where-Object { $_.FullName -notmatch '\\__pycache__\\' -and $_.Extension -ne '.pyc' } |
		ForEach-Object {
			# Rutas con "/" dentro del zip, como espera NVDA.
			$rel = $_.FullName.Substring($src.Length + 1).Replace('\', '/')
			[System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $_.FullName, $rel) | Out-Null
		}
} finally {
	$zip.Dispose()
}

Write-Host "Generado: $out"
