# Builds a portable Windows package: unzip anywhere and double-click, with no Python,
# .NET or source checkout on the target PC. Works in Windows PowerShell 5.1 and PowerShell 7.
#
#   desktop\launch.cmd package            (or: desktop\package-windows.ps1 [-OutputDirectory <new folder>])
#
# The package carries its own Python with only the runtime dependencies, the backend,
# the shared UI and a self-contained .NET host. It still needs the Microsoft Edge
# WebView2 Runtime, which Windows 11 includes. It is unsigned.
[CmdletBinding()]
param(
    # Defaults to build\portable in the project. Resolved below, because Windows PowerShell 5.1
    # leaves $PSScriptRoot empty inside param() when the script is started with -File.
    [string]$OutputDirectory,
    [string]$PythonPath = $env:MEETING_COPILOT_PYTHON,
    # Smaller package that needs the .NET 8 Desktop Runtime on the target PC.
    [switch]$FrameworkDependent,
    [switch]$Plan
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$desktopRoot = $PSScriptRoot
$projectRoot = Split-Path -Parent $desktopRoot
$projectFile = Join-Path $desktopRoot 'windows\MeetingCopilot.Windows.csproj'
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $projectRoot 'build\portable' }
$destination = [IO.Path]::GetFullPath($OutputDirectory)
$packageName = 'Meeting Copilot'
$zip = Join-Path $destination 'MeetingCopilot-windows-x64.zip'

if (-not $PythonPath) { $PythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe' }
$PythonPath = [IO.Path]::GetFullPath($PythonPath)
$environment = Split-Path -Parent (Split-Path -Parent $PythonPath)
$settings = Join-Path $environment 'pyvenv.cfg'
if (-not (Test-Path -LiteralPath $settings -PathType Leaf)) {
    throw "PythonPath must point to a virtual environment's Scripts\python.exe: $PythonPath"
}
# The interpreter the environment was created from is copied whole, so the package needs no installed Python.
$basePython = $null
foreach ($line in Get-Content -LiteralPath $settings) {
    if ($line -match '^\s*home\s*=\s*(.+?)\s*$') { $basePython = $Matches[1] }
}
if (-not $basePython -or -not (Test-Path -LiteralPath (Join-Path $basePython 'python.exe') -PathType Leaf)) {
    throw "Could not find the base Python of $environment (pyvenv.cfg 'home')."
}
if (Test-Path -LiteralPath $destination) {
    throw "Output directory already exists: $destination. Choose a new -OutputDirectory; this script never overwrites a package."
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'Packaging installs the runtime dependencies with uv. Install it from https://docs.astral.sh/uv/ and run this again.'
}

$stage = "$destination.staging-$([guid]::NewGuid().ToString('N'))"
$root = Join-Path $stage $packageName
$app = Join-Path $root 'app'
$runtime = Join-Path $root 'Runtime'
$python = Join-Path $runtime 'python'

if ($Plan) {
    [pscustomobject]@{
        Project = $projectRoot
        BasePython = $basePython
        Package = (Join-Path $destination $packageName)
        Zip = $zip
        SelfContained = -not $FrameworkDependent
        Note = 'No files were written. Remove -Plan to build the portable package.'
    } | Format-List
    exit 0
}

function Get-FolderMegabytes([string]$Path) {
    $bytes = (Get-ChildItem -LiteralPath $Path -Recurse -File | Measure-Object -Property Length -Sum).Sum
    return [math]::Round($bytes / 1MB)
}

New-Item -ItemType Directory -Path $runtime -Force | Out-Null
try {
    Write-Host '1/4 Building the Windows host...'
    $publish = @('publish', $projectFile, '--configuration', 'Release', '--output', $app, '--runtime', 'win-x64')
    if ($FrameworkDependent) { $publish += @('--self-contained', 'false') } else { $publish += @('--self-contained', 'true') }
    & dotnet @publish | Out-Host
    if (-not (Test-Path -LiteralPath (Join-Path $app 'MeetingCopilot.Windows.exe') -PathType Leaf)) {
        throw 'dotnet publish did not produce the app. See the output above.'
    }

    Write-Host '2/4 Copying Python...'
    # Development-only parts of a Python installation that the app never loads.
    $skipFolders = @('tcl', 'include', 'libs', 'Scripts', 'share', 'Doc')
    $skipLibraries = @('site-packages', 'idlelib', 'tkinter', 'turtledemo', 'ensurepip', 'test', 'lib2to3', '__pycache__')
    New-Item -ItemType Directory -Path (Join-Path $python 'Lib\site-packages') -Force | Out-Null
    foreach ($item in Get-ChildItem -LiteralPath $basePython -Force) {
        if ($item.PSIsContainer -and $skipFolders -contains $item.Name) { continue }
        if ($item.Name -ne 'Lib') { Copy-Item -LiteralPath $item.FullName -Destination $python -Recurse; continue }
        foreach ($library in Get-ChildItem -LiteralPath $item.FullName -Force) {
            if ($skipLibraries -contains $library.Name -or $library.Name -eq 'EXTERNALLY-MANAGED') { continue }
            Copy-Item -LiteralPath $library.FullName -Destination (Join-Path $python 'Lib') -Recurse
        }
    }

    Write-Host '3/4 Installing the runtime dependencies (no test tools)...'
    & uv pip install --quiet --python (Join-Path $python 'python.exe') --break-system-packages `
        -r (Join-Path $projectRoot 'pyproject.toml') --extra audio --extra desktop | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Installing the Python dependencies failed. See the output above.' }
    New-Item -ItemType Directory -Path (Join-Path $runtime 'desktop') | Out-Null
    Copy-Item -LiteralPath (Join-Path $projectRoot 'src') -Destination (Join-Path $runtime 'src') -Recurse
    Copy-Item -LiteralPath (Join-Path $desktopRoot 'bridge.py') -Destination (Join-Path $runtime 'desktop\bridge.py')
    Copy-Item -LiteralPath (Join-Path $desktopRoot 'minutes.py') -Destination (Join-Path $runtime 'desktop\minutes.py')
    Copy-Item -LiteralPath (Join-Path $desktopRoot 'ui') -Destination (Join-Path $runtime 'desktop\ui') -Recurse
    Get-ChildItem -LiteralPath $root -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force
    $check = 'import importlib.util as u,sys;m=[x for x in sys.argv[1:] if u.find_spec(x) is None];sys.exit(chr(44).join(m) or 0)'
    $previous = $env:PYTHONPATH
    $env:PYTHONPATH = Join-Path $runtime 'src'
    try {
        & (Join-Path $python 'python.exe') -B -c $check meeting_copilot langgraph litellm mcp faster_whisper pyaudiowpatch keyring
        if ($LASTEXITCODE -ne 0) { throw 'The packaged Python is missing a required module (listed above).' }
    }
    finally { $env:PYTHONPATH = $previous }
    Set-Content -LiteralPath (Join-Path $app 'backend-root.txt') -Value '..\Runtime' -NoNewline -Encoding ascii
    Set-Content -LiteralPath (Join-Path $root 'Start Meeting Copilot.cmd') -Encoding ascii -Value @(
        '@echo off',
        'rem Double-click to open Meeting Copilot. Nothing is installed; delete this folder to remove it.',
        'start "" "%~dp0app\MeetingCopilot.Windows.exe"'
    )
    Set-Content -LiteralPath (Join-Path $root 'README.txt') -Encoding ascii -Value @(
        'Meeting Copilot (portable, unsigned developer build)',
        '',
        'Double-click "Start Meeting Copilot.cmd". Nothing is installed and no Python or .NET is needed.',
        'Open Settings in the app once to choose a model and paste its API key.',
        '',
        'Needs Windows 10/11 x64 with the Microsoft Edge WebView2 Runtime (included in Windows 11).',
        'Meetings are kept in %LOCALAPPDATA%\Meeting Copilot\Workspace; the API key is kept in Windows',
        'Credential Manager. The first recording or audio import downloads the speech model once.',
        'Windows may warn that the publisher is unknown because the build is not code signed.'
    )

    Write-Host '4/4 Compressing...'
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $stagedZip = "$stage.zip"
    [IO.Compression.ZipFile]::CreateFromDirectory($stage, $stagedZip, [IO.Compression.CompressionLevel]::Optimal, $false)
    Move-Item -LiteralPath $stage -Destination $destination
    Move-Item -LiteralPath $stagedZip -Destination $zip
    [pscustomobject]@{
        Package = (Join-Path $destination $packageName)
        UnpackedMB = Get-FolderMegabytes (Join-Path $destination $packageName)
        Zip = $zip
        ZipMB = [math]::Round((Get-Item -LiteralPath $zip).Length / 1MB)
    } | Format-List
}
catch {
    Write-Error "The package was not built. The staging directory was kept for inspection: $stage"
    throw
}
