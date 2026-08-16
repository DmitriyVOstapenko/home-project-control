$ErrorActionPreference = 'Stop'

$serverPath = Join-Path $PSScriptRoot 'server.mjs'
$nodeCandidates = @(
    (Join-Path $env:LOCALAPPDATA 'OpenAI\Codex\bin\node.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\OpenAI Codex\bin\node.exe')
)

$nodePath = $nodeCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $nodePath) {
    $nodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
    if ($nodeCommand) {
        $nodePath = $nodeCommand.Source
    }
}

if (-not $nodePath) {
    [Console]::Error.WriteLine('Home Project Control: Node.js runtime was not found.')
    exit 1
}

& $nodePath $serverPath
exit $LASTEXITCODE
