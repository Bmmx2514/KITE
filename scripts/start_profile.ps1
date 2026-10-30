param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('baseline', 'injection', 'public')]
    [string]$Profile
)

$ports = @{ baseline = 9621; injection = 9622; public = 9623 }
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$env:PYTHONIOENCODING = 'utf-8'
$env:PORT = [string]$ports[$Profile]
$env:WORKING_DIR = "./.lightrag/$Profile"
$env:INPUT_DIR = "./.lightrag/inputs-$Profile"
$env:LOG_DIR = "./.lightrag/logs-$Profile"
New-Item -ItemType Directory -Force (Join-Path $root ".lightrag/logs-$Profile") | Out-Null
Set-Location $root
& (Join-Path $root '.venv/Scripts/lightrag-server.exe')
