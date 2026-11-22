[CmdletBinding()]
param(
    [string]$OutputDirectory = (Join-Path $PSScriptRoot '..\build\windows'),
    [string]$PythonPath = $env:MEETING_COPILOT_PYTHON,
    [switch]$Plan
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$desktopRoot = $PSScriptRoot
$projectRoot = Split-Path -Parent $desktopRoot
$projectFile = Join-Path $desktopRoot 'windows\MeetingCopilot.Windows.csproj'
$destination = [IO.Path]::GetFullPath($OutputDirectory)

if (-not $PythonPath) {
    $PythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
}
$PythonPath = [IO.Path]::GetFullPath($PythonPath)
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Python environment was not found: $PythonPath. Pass -PythonPath or set MEETING_COPILOT_PYTHON."
}
if (Test-Path -LiteralPath $destination) {
    throw "Output directory already exists: $destination. Choose a new -OutputDirectory; this script never overwrites a bundle."
}

$pythonScripts = Split-Path -Parent $PythonPath
$pythonEnvironment = Split-Path -Parent $pythonScripts
if (-not (Test-Path -LiteralPath (Join-Path $pythonEnvironment 'pyvenv.cfg') -PathType Leaf)) {
    throw "PythonPath must point to a virtual environment's Scripts\\python.exe: $PythonPath"
}

$stage = "$destination.staging-$([guid]::NewGuid().ToString('N'))"
$app = Join-Path $stage 'Meeting Copilot'
$runtime = Join-Path $stage 'Runtime'

if ($Plan) {
    [pscustomobject]@{
        Project = $projectRoot
        PythonEnvironment = $pythonEnvironment
        Bundle = $destination
        App = $app
        Runtime = $runtime
        Note = 'No files were written. Remove -Plan to publish an unpackaged developer bundle.'
    } | Format-List
    exit 0
}

New-Item -ItemType Directory -Path $stage | Out-Null
try {
    dotnet publish $projectFile --configuration Release --output $app --self-contained false
    New-Item -ItemType Directory -Path (Join-Path $runtime 'desktop') | Out-Null
    Copy-Item -LiteralPath $pythonEnvironment -Destination (Join-Path $runtime '.venv') -Recurse
    Copy-Item -LiteralPath (Join-Path $projectRoot 'src') -Destination (Join-Path $runtime 'src') -Recurse
    Copy-Item -LiteralPath (Join-Path $desktopRoot 'bridge.py') -Destination (Join-Path $runtime 'desktop\bridge.py')
    Copy-Item -LiteralPath (Join-Path $desktopRoot 'minutes.py') -Destination (Join-Path $runtime 'desktop\minutes.py')
    Copy-Item -LiteralPath (Join-Path $desktopRoot 'ui') -Destination (Join-Path $runtime 'desktop\ui') -Recurse
    Set-Content -LiteralPath (Join-Path $app 'backend-root.txt') -Value '..\Runtime' -NoNewline
    Move-Item -LiteralPath $stage -Destination $destination
    Write-Output (Join-Path $destination 'Meeting Copilot\MeetingCopilot.Windows.exe')
}
catch {
    Write-Error "The bundle was not published. The staging directory was retained for inspection: $stage"
    throw
}
