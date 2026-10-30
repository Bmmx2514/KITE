param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('baseline', 'injection', 'public')]
    [string]$Profile,
    [ValidatePattern('^[a-z][a-z0-9_-]*$')]
    [string]$Role,
    [int]$Port
)

$ports = @{ baseline = 9621; injection = 9622; public = 9623 }
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$indexName = $Profile
if ($Role) {
    if (-not $Port) { throw 'A role-scoped index needs its own -Port.' }
    $indexName = "$Profile-$Role"
}
$env:PYTHONIOENCODING = 'utf-8'
$env:PORT = [string]$(if ($Port) { $Port } else { $ports[$Profile] })
$env:WORKING_DIR = "./.lightrag/$indexName"
$env:INPUT_DIR = "./.lightrag/inputs-$indexName"
$env:LOG_DIR = "./.lightrag/logs-$indexName"
New-Item -ItemType Directory -Force (Join-Path $root ".lightrag/logs-$indexName") | Out-Null
Set-Location $root
& (Join-Path $root '.venv/Scripts/lightrag-server.exe')
