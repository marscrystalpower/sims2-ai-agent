param(
    [Parameter(Mandatory=$true)][string]$Mods,
    [string]$GameExe,
    [string]$Python = 'python',
    [int]$Port = 8765,
    [string]$Neighborhood,
    [string]$Names
)
$ErrorActionPreference = 'Stop'
$modsPath = (Resolve-Path -LiteralPath $Mods).Path
if (-not $GameExe) { $GameExe = Join-Path (Split-Path $modsPath -Parent) 'Sims2EP9RPC.exe' }
$gamePath = (Resolve-Path -LiteralPath $GameExe).Path
$gameName = [IO.Path]::GetFileNameWithoutExtension($gamePath)
$games = @(Get-Process -Name $gameName -ErrorAction Stop)
if ($games.Count -ne 1) { throw 'Expected exactly one game process. Close extra instances first.' }
if ($games[0].Path -ne $gamePath) { throw 'Running game does not match the supplied executable.' }
if ([bool]$Neighborhood -ne [bool]$Names) { throw 'Supply Neighborhood and Names together, or omit both.' }
$apiArgs = @("$PSScriptRoot/TS2Bridge-api.py", '--mods', $modsPath, '--port', "$Port", '--alarm-pid', "$($games[0].Id)", '--alarm-exe', $gamePath)
if ($Names) { $apiArgs += @('--neighborhood', $Neighborhood, '--names', (Resolve-Path -LiteralPath $Names).Path) }
& $Python @apiArgs
exit $LASTEXITCODE
