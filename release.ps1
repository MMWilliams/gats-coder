# release.ps1 — build the wheel and publish it into the kyberos site.
#
# Automates the four-step release chore: reads the version from
# pyproject.toml, builds the wheel, copies it to <site>\public\ledger,
# and rewrites latest.json (version/wheel/sha256/released) plus the
# WHEEL constant in src\landing\Ledger.jsx. Commit and deploy the site
# yourself afterwards.
#
#   .\release.ps1                 # site defaults to C:\Users\reese\kyberos
#   .\release.ps1 -Site D:\kyberos

param([string]$Site = 'C:\Users\reese\kyberos')

$ErrorActionPreference = 'Stop'
$repo = $PSScriptRoot
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

$pyproject = Get-Content (Join-Path $repo 'pyproject.toml') -Raw
if ($pyproject -notmatch '(?m)^version\s*=\s*"([^"]+)"') {
    throw 'no version field in pyproject.toml'
}
$version = $Matches[1]
Write-Host "==> releasing ledger-agent $version"

# Keep the journal-stamped AGENT_VERSION in lockstep with the package.
$initPath = Join-Path $repo 'ledger\__init__.py'
$init = [System.IO.File]::ReadAllText($initPath)
$synced = $init -replace 'AGENT_VERSION = "[^"]+"', "AGENT_VERSION = `"$version`""
if ($synced -ne $init) {
    [System.IO.File]::WriteAllText($initPath, $synced, $utf8NoBom)
    Write-Host "    synced AGENT_VERSION -> $version"
}

Remove-Item -Recurse -Force (Join-Path $repo 'dist') -ErrorAction SilentlyContinue
python -m pip wheel $repo --no-deps -w (Join-Path $repo 'dist')
if ($LASTEXITCODE -ne 0) { throw 'wheel build failed' }
$wheel = @(Get-ChildItem (Join-Path $repo "dist\ledger_agent-$version-*.whl"))
if ($wheel.Count -ne 1) { throw "expected exactly one wheel for $version in dist\" }
$wheel = $wheel[0]

$pub = Join-Path $Site 'public\ledger'
if (-not (Test-Path $pub)) { throw "$pub does not exist" }
Get-ChildItem (Join-Path $pub 'ledger_agent-*.whl') |
    Where-Object Name -ne $wheel.Name | Remove-Item -Confirm:$false
Copy-Item $wheel.FullName $pub -Force
$sha = (Get-FileHash (Join-Path $pub $wheel.Name) -Algorithm SHA256).Hash.ToLower()

# latest.json — written without a BOM: install.sh parses it with json.load,
# which rejects BOM-prefixed input.
$latestPath = Join-Path $pub 'latest.json'
$meta = Get-Content $latestPath -Raw | ConvertFrom-Json
$meta.version = $version
$meta.wheel = "/ledger/$($wheel.Name)"
$meta.sha256 = $sha
$meta.released = Get-Date -Format 'yyyy-MM-dd'
[System.IO.File]::WriteAllText($latestPath, (($meta | ConvertTo-Json) + "`n"), $utf8NoBom)

$jsx = Join-Path $Site 'src\landing\Ledger.jsx'
$content = [System.IO.File]::ReadAllText($jsx)
$updated = $content -replace 'ledger_agent-[\d.]+-py3-none-any\.whl', $wheel.Name
if ($updated -eq $content -and $content -notmatch [regex]::Escape($wheel.Name)) {
    throw "could not find a wheel reference to update in $jsx"
}
[System.IO.File]::WriteAllText($jsx, $updated, $utf8NoBom)

Write-Host ''
Write-Host "==> published $($wheel.Name)"
Write-Host "    sha256 $sha"
Write-Host "    updated latest.json + Ledger.jsx"
Write-Host '    next: commit + push both repos (site deploy publishes the wheel)'
