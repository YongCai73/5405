# Meeting Copilot launcher for Windows. Works in Windows PowerShell 5.1 and PowerShell 7.
# Double-click "Start Meeting Copilot.cmd" in the project folder, or call a command directly:
#   desktop\launch.cmd [run|demo|build|package|doctor|set-key|config|test|help]
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('run', 'demo', 'build', 'package', 'doctor', 'set-key', 'config', 'test', 'help')]
    [string]$Command = 'run'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$desktopRoot = $PSScriptRoot
$projectRoot = Split-Path -Parent $desktopRoot
$bundle = Join-Path $projectRoot 'build\windows'
$exe = Join-Path $bundle 'Meeting Copilot\MeetingCopilot.Windows.exe'
$bundleMarker = Join-Path $bundle 'launch-bundle.txt'
$appData = Join-Path $env:LOCALAPPDATA 'Meeting Copilot'
$workspace = Join-Path $appData 'Workspace'
if ($env:MEETING_COPILOT_DESKTOP_DATA_DIR) { $workspace = $env:MEETING_COPILOT_DESKTOP_DATA_DIR }
$demoWorkspace = Join-Path $appData 'Demo Workspace'

function Write-Check([string]$Name, [bool]$Ok, [string]$Detail, [string]$Fix = '') {
    $state = 'MISSING'
    if ($Ok) { $state = 'OK' }
    Write-Host ('[{0,-7}] {1}: {2}' -f $state, $Name, $Detail)
    if (-not $Ok -and $Fix) { Write-Host "          Fix: $Fix" }
}

function Get-ProjectPython {
    foreach ($candidate in @($env:MEETING_COPILOT_PYTHON, (Join-Path $projectRoot '.venv\Scripts\python.exe'))) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return [IO.Path]::GetFullPath($candidate)
        }
    }
    return $null
}

function Get-InstallCommand([string]$Python, [string]$Packages) {
    # Environments created by uv have no pip of their own.
    if (Get-Command uv -ErrorAction SilentlyContinue) { return "uv pip install --python `"$Python`" $Packages" }
    return "`"$Python`" -m pip install $Packages"
}

function Get-DotnetLines([string]$Argument) {
    if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) { return @() }
    try { return @(& dotnet $Argument 2>$null) } catch { return @() }
}

function Test-DotnetSdk {
    # The host targets net8.0-windows, which any SDK from 8 onward can build.
    foreach ($line in (Get-DotnetLines '--list-sdks')) {
        if ($line -match '^(\d+)\.' -and [int]$Matches[1] -ge 8) { return $true }
    }
    return $false
}

function Test-DesktopRuntime {
    foreach ($line in (Get-DotnetLines '--list-runtimes')) {
        if ($line -match '^Microsoft\.WindowsDesktop\.App 8\.') { return $true }
    }
    return $false
}

function Test-WebView2 {
    $client = 'Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
    foreach ($key in @("HKLM:\SOFTWARE\WOW6432Node\$client", "HKLM:\SOFTWARE\$client", "HKCU:\Software\$client")) {
        $value = Get-ItemProperty -Path $key -Name pv -ErrorAction SilentlyContinue
        if ($value -and $value.pv -and $value.pv -ne '0.0.0.0') { return $true }
    }
    return $false
}

function Get-SourceFingerprint([string]$Python) {
    # Changes when the backend, shared UI, Windows host or installed packages change.
    $files = @(Get-ChildItem -LiteralPath (Join-Path $projectRoot 'src') -Recurse -File |
        Where-Object { $_.FullName -notmatch '\\__pycache__\\' })
    $files += @(Get-ChildItem -LiteralPath $desktopRoot -Recurse -File |
        Where-Object { $_.FullName -notmatch '\\windows\\(bin|obj)\\' -and $_.FullName -notmatch '\\__pycache__\\' })
    $latest = ($files | Measure-Object -Property LastWriteTimeUtc -Maximum).Maximum.Ticks
    $packages = 0
    if ($Python) {
        $sitePackages = Join-Path (Split-Path -Parent (Split-Path -Parent $Python)) 'Lib\site-packages'
        if (Test-Path -LiteralPath $sitePackages) { $packages = (Get-Item -LiteralPath $sitePackages).LastWriteTimeUtc.Ticks }
    }
    return '{0}:{1}:{2}' -f $files.Count, $latest, $packages
}

function Test-BundleCurrent([string]$Python) {
    if (-not (Test-Path -LiteralPath $bundleMarker -PathType Leaf)) { return $false }
    $built = (Get-Content -LiteralPath $bundleMarker -Raw).Trim()
    $now = Get-SourceFingerprint $Python
    if (-not $Python) {
        # Without a Python environment only the source part can be compared.
        $built = $built.Substring(0, $built.LastIndexOf(':'))
        $now = $now.Substring(0, $now.LastIndexOf(':'))
    }
    return $built -eq $now
}

function Test-AppRunning {
    $running = @(Get-Process -Name 'MeetingCopilot.Windows' -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith($bundle, [StringComparison]::OrdinalIgnoreCase) })
    return $running.Count -gt 0
}

function Invoke-Python([string]$Python, [string[]]$Arguments) {
    $previous = $env:PYTHONPATH
    $env:PYTHONPATH = Join-Path $projectRoot 'src'
    try {
        # Out-Host keeps the output on the console instead of in the return value.
        & $Python @Arguments | Out-Host
        return $LASTEXITCODE
    }
    finally { $env:PYTHONPATH = $previous }
}

function Invoke-Build {
    $python = Get-ProjectPython
    if (-not $python) {
        throw "No Python environment was found. Create it from '$projectRoot': python -m venv .venv ; .venv\Scripts\python -m pip install -e '.[audio,desktop]'"
    }
    if (-not (Test-DotnetSdk)) {
        throw 'Building the Windows app needs the .NET 8 SDK (one-time). Install it with: winget install Microsoft.DotNet.SDK.8'
    }
    if (Test-AppRunning) { throw 'Close Meeting Copilot before rebuilding it.' }
    if ((Test-Path -LiteralPath $bundle) -and -not (Test-Path -LiteralPath $bundleMarker -PathType Leaf)) {
        throw "The folder '$bundle' was not created by this launcher. Move or delete it, then build again."
    }

    $next = "$bundle.next"
    $previous = "$bundle.previous"
    foreach ($leftover in @($next, $previous)) {
        if (Test-Path -LiteralPath $leftover) { Remove-Item -LiteralPath $leftover -Recurse -Force }
    }
    $fingerprint = Get-SourceFingerprint $python
    Write-Host 'Building Meeting Copilot. The first build copies the Python environment and can take a few minutes...'
    & (Join-Path $desktopRoot 'build-windows.ps1') -OutputDirectory $next -PythonPath $python | Out-Host
    if (-not (Test-Path -LiteralPath (Join-Path $next 'Meeting Copilot\MeetingCopilot.Windows.exe') -PathType Leaf)) {
        throw "The build did not produce the app. See the dotnet output above. Partial output: $next"
    }
    Set-Content -LiteralPath (Join-Path $next 'launch-bundle.txt') -Value $fingerprint -NoNewline -Encoding ascii
    if (Test-Path -LiteralPath $bundle) { Move-Item -LiteralPath $bundle -Destination $previous }
    Move-Item -LiteralPath $next -Destination $bundle
    if (Test-Path -LiteralPath $previous) { Remove-Item -LiteralPath $previous -Recurse -Force }
    Write-Host "Built $exe"
}

function Start-App([bool]$Demo) {
    $python = Get-ProjectPython
    $present = Test-Path -LiteralPath $exe -PathType Leaf
    if (-not $present) {
        Invoke-Build
    }
    elseif (-not (Test-BundleCurrent $python)) {
        if ((Test-DotnetSdk) -and $python -and -not (Test-AppRunning)) {
            Write-Host 'The source changed since the last build. Rebuilding...'
            Invoke-Build
        }
        else {
            Write-Warning 'The source changed since the last build, but it cannot be rebuilt right now (app is open, or the .NET 8 SDK / Python environment is missing). Opening the existing build.'
        }
    }
    if (-not (Test-DesktopRuntime)) {
        throw 'The .NET 8 Desktop Runtime is missing. Install it with: winget install Microsoft.DotNet.DesktopRuntime.8'
    }
    if (-not (Test-WebView2)) {
        throw 'The Microsoft Edge WebView2 Runtime is missing. Install it with: winget install Microsoft.EdgeWebView2Runtime'
    }
    if ($Demo) {
        # A separate, configuration-free workspace: the sample meeting, local answers, no API key.
        $env:MEETING_COPILOT_DESKTOP_DATA_DIR = $demoWorkspace
        Remove-Item Env:MEETING_COPILOT_DESKTOP_CONFIG -ErrorAction SilentlyContinue
        $env:MEETING_COPILOT_KEYRING = '0'
    }
    # The bundle carries its own backend; source-tree overrides would defeat that.
    Remove-Item Env:MEETING_COPILOT_DESKTOP_ROOT -ErrorAction SilentlyContinue
    Remove-Item Env:MEETING_COPILOT_PYTHON -ErrorAction SilentlyContinue
    Start-Process -FilePath $exe -WorkingDirectory (Split-Path -Parent $exe)
    Write-Host 'Meeting Copilot is opening.'
}

function Show-Doctor {
    $python = Get-ProjectPython
    $problems = 0
    Write-Host "Project:   $projectRoot"
    Write-Host "Workspace: $workspace"
    Write-Host ''

    Write-Check 'Python environment' ([bool]$python) "$python" "from '$projectRoot': python -m venv .venv ; .venv\Scripts\python -m pip install -e '.[audio,desktop]'"
    if (-not $python) { $problems++ }
    else {
        $probe = 'import importlib.util as u,sys;m=sys.argv[1:];print(chr(44).join(x for x in m if u.find_spec(x) is None))'
        $required = @('meeting_copilot', 'langgraph', 'litellm', 'mcp', 'faster_whisper', 'pyaudiowpatch')
        $previous = $env:PYTHONPATH
        $env:PYTHONPATH = Join-Path $projectRoot 'src'
        try { $missing = (& $python -c $probe @required keyring | Out-String).Trim() }
        finally { $env:PYTHONPATH = $previous }
        $missingRequired = @($missing.Split(',') | Where-Object { $_ -and $_ -ne 'keyring' })
        $packageDetail = 'all present'
        if ($missingRequired.Count -gt 0) { $packageDetail = 'missing ' + ($missingRequired -join ', ') }
        Write-Check 'Python packages' ($missingRequired.Count -eq 0) $packageDetail ("from '$projectRoot': " + (Get-InstallCommand $python "-e '.[audio,desktop]'"))
        if ($missingRequired.Count -gt 0) { $problems++ }
        $hasKeyring = -not ($missing.Split(',') -contains 'keyring')
        Write-Check 'Saved-key support (keyring)' $hasKeyring 'needed only to save an API key for double-click launches' (Get-InstallCommand $python 'keyring')
    }

    $sdk = Test-DotnetSdk
    Write-Check '.NET 8 SDK' $sdk 'needed only to build or rebuild the app' 'winget install Microsoft.DotNet.SDK.8'
    $runtime = Test-DesktopRuntime
    Write-Check '.NET 8 Desktop Runtime' $runtime 'needed to open the app' 'winget install Microsoft.DotNet.DesktopRuntime.8'
    if (-not $runtime) { $problems++ }
    $webview = Test-WebView2
    Write-Check 'WebView2 Runtime' $webview 'needed to open the app' 'winget install Microsoft.EdgeWebView2Runtime'
    if (-not $webview) { $problems++ }

    $built = Test-Path -LiteralPath $exe -PathType Leaf
    if (-not $built) {
        Write-Check 'App build' $false 'not built yet' 'desktop\launch.cmd build   (needs the .NET 8 SDK once)'
        if (-not $sdk) { $problems++ }
    }
    elseif (Test-BundleCurrent $python) { Write-Check 'App build' $true 'up to date' }
    else { Write-Check 'App build' $true 'present, but the source changed since it was built (run rebuilds it when the SDK is installed)' }

    $config = Join-Path $workspace 'config.json'
    if (Test-Path -LiteralPath $config -PathType Leaf) {
        Write-Check 'Configuration' $true $config
    }
    else {
        Write-Check 'Configuration' $true 'none; the app uses local sample mode (run "config" to create one for a real model)'
    }
    if ($python) {
        Write-Host ''
        Write-Host 'API key (only needed for a real model):'
        $arguments = @('-m', 'meeting_copilot.credentials', 'status')
        if (Test-Path -LiteralPath $config -PathType Leaf) { $arguments += @('--config', $config) }
        [void](Invoke-Python $python $arguments)
    }
    Write-Host ''
    if ($problems -eq 0) { Write-Host 'Ready: double-click "Start Meeting Copilot.cmd".' }
    else { Write-Host "$problems item(s) above must be fixed before the app can open." }
    return $problems
}

function Show-Help {
    Write-Host @'
Meeting Copilot launcher

  Double-click "Start Meeting Copilot.cmd", or run:  desktop\launch.cmd <command>

  run       Open the app (default). Builds it first when needed.
  demo      Open the app on a separate sample workspace. No API key or configuration.
  build     Build or rebuild the app bundle in build\windows.
  package   Build a portable zip for another PC (own Python and .NET) in a new build\portable-<time> folder.
  doctor    Check everything the app needs and print how to fix what is missing.
  set-key   Optional: save the API key from a terminal. Settings in the app does the same.
  config    Optional: open the workspace config.json. Settings in the app writes the model choice there.
  test      Run the Python tests and the shared desktop UI tests.
  help      Show this text.

The model, its address and the API key are set once in the app under Settings.
No environment variables are required. These remain optional overrides:
  MEETING_COPILOT_DESKTOP_DATA_DIR, MEETING_COPILOT_DESKTOP_CONFIG, MEETING_COPILOT_PYTHON,
  and the variable named by api_key_env in config.json (it wins over the saved key).
'@
}

try {
switch ($Command) {
    'help' { Show-Help }
    'run' { Start-App $false }
    'demo' { Start-App $true }
    'build' { Invoke-Build }
    'package' {
        $python = Get-ProjectPython
        if (-not $python) { throw 'No Python environment was found. Run: desktop\launch.cmd doctor' }
        if (-not (Test-DotnetSdk)) { throw 'Packaging needs the .NET 8 SDK. Install it with: winget install Microsoft.DotNet.SDK.8' }
        $target = Join-Path $projectRoot ('build\portable-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
        & (Join-Path $desktopRoot 'package-windows.ps1') -OutputDirectory $target -PythonPath $python | Out-Host
    }
    'doctor' { if ((Show-Doctor) -gt 0) { exit 1 } }
    'set-key' {
        $python = Get-ProjectPython
        if (-not $python) { throw 'No Python environment was found. Run: desktop\launch.cmd doctor' }
        $arguments = @('-m', 'meeting_copilot.credentials', 'set')
        $config = Join-Path $workspace 'config.json'
        if ($env:MEETING_COPILOT_DESKTOP_CONFIG) { $config = $env:MEETING_COPILOT_DESKTOP_CONFIG }
        if (Test-Path -LiteralPath $config -PathType Leaf) { $arguments += @('--config', $config) }
        $code = Invoke-Python $python $arguments
        if ($code -ne 0) { exit $code }
        Write-Host 'Restart Meeting Copilot if it is open.'
    }
    'config' {
        $config = Join-Path $workspace 'config.json'
        if (-not (Test-Path -LiteralPath $config -PathType Leaf)) {
            New-Item -ItemType Directory -Path $workspace -Force | Out-Null
            Copy-Item -LiteralPath (Join-Path $projectRoot 'config.example.json') -Destination $config
            Write-Host "Created $config from config.example.json (local sample mode until you change provider)."
        }
        Write-Host "Opening $config. Restart Meeting Copilot after saving."
        Start-Process -FilePath notepad.exe -ArgumentList ('"{0}"' -f $config)
    }
    'test' {
        $python = Get-ProjectPython
        if (-not $python) { throw 'No Python environment was found. Run: desktop\launch.cmd doctor' }
        Push-Location $projectRoot
        try {
            $code = Invoke-Python $python @('-m', 'pytest', '-q')
            if (Get-Command node -ErrorAction SilentlyContinue) {
                & node --test 'tests/test_desktop_audio_ui.mjs'
                if ($LASTEXITCODE -ne 0) { $code = $LASTEXITCODE }
            }
            else { Write-Warning 'Node.js was not found; the shared desktop UI tests were skipped.' }
        }
        finally { Pop-Location }
        if ($code -ne 0) { exit $code }
    }
}
}
catch {
    Write-Host ''
    Write-Host "Meeting Copilot could not continue: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'Run "desktop\launch.cmd doctor" to see what is missing.'
    exit 1
}
