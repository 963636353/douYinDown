param(
    [switch]$PauseOnExit,
    [switch]$OpenReleaseFolder,
    [switch]$SmokeTest,
    [switch]$SkipZip,
    [switch]$CheckOnly,
    [string]$Version = (Get-Date -Format 'yyyyMMdd-HHmm')
)

$ErrorActionPreference = 'Stop'

function Write-Step {
    param([string]$Message)
    Write-Host ''
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Resolve-BuildPython {
    param([string]$Root)

    foreach ($environmentName in @('.venv', 'venv')) {
        $candidate = Join-Path $Root "$environmentName\Scripts\python.exe"
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }
    throw "Python virtual environment not found. Expected .venv\Scripts\python.exe or venv\Scripts\python.exe under $Root. Create an environment and install requirements.txt first."
}

function Resolve-InWorkspace {
    param(
        [string]$Path,
        [string]$Root
    )

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $rootPath = [System.IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
    if (-not $fullPath.StartsWith($rootPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to operate outside workspace: $fullPath"
    }
    return $fullPath
}

function Remove-GeneratedDir {
    param(
        [string]$RelativePath,
        [string]$Root
    )

    $target = Resolve-InWorkspace -Path (Join-Path $Root $RelativePath) -Root $Root
    if (Test-Path -LiteralPath $target) {
        Write-Host "Removing $target"
        Remove-Item -LiteralPath $target -Recurse -Force
    }
}

function Remove-GeneratedPath {
    param(
        [string]$RelativePath,
        [string]$Root,
        [switch]$ContinueOnError
    )

    $target = Resolve-InWorkspace -Path (Join-Path $Root $RelativePath) -Root $Root
    if (Test-Path -LiteralPath $target) {
        Write-Host "Removing $target"
        try {
            Remove-Item -LiteralPath $target -Recurse -Force
        }
        catch {
            if ($ContinueOnError) {
                Write-Warning "Could not remove ${target}: $($_.Exception.Message)"
            }
            else {
                throw
            }
        }
    }
}

$exitCode = 0
$locationPushed = $false

try {
    $Root = Split-Path -Parent $MyInvocation.MyCommand.Path
    Push-Location -LiteralPath $Root
    $locationPushed = $true
    $AppName = 'DouyinDownloader'
    $DistDir = Join-Path $Root "dist\$AppName"
    $ReleaseDir = Join-Path $Root 'release'

    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'

    Write-Step 'Checking Python virtual environment'
    $Python = Resolve-BuildPython -Root $Root
    Write-Host "Using Python: $Python"
    & $Python -c "import sys; print('Python ' + sys.version.split()[0])"
    if ($LASTEXITCODE -ne 0) {
        throw 'The selected Python environment cannot run. Recreate it and install requirements.txt.'
    }

    Write-Step 'Checking client dependencies'
    & $Python -c "import importlib.util, sys; names = ['f2', 'PyQt5', 'qfluentwidgets', 'requests', 'selenium', 'undetected_chromedriver']; missing = [name for name in names if importlib.util.find_spec(name) is None]; print('Missing modules: ' + ', '.join(missing) if missing else 'Client dependencies found'); sys.exit(bool(missing))"
    if ($LASTEXITCODE -ne 0) {
        throw "Client dependencies are missing. Run: & '$Python' -m pip install -r requirements.txt"
    }

    & $Python -c "import importlib.util, sys; sys.exit(importlib.util.find_spec('PyInstaller') is None)"
    $hasPyInstaller = $LASTEXITCODE -eq 0
    if ($CheckOnly) {
        if (-not $hasPyInstaller) {
            Write-Host 'PyInstaller is not installed; the normal build will install it automatically.'
        }
        Write-Host 'Environment check passed. No packages installed or build outputs changed.' -ForegroundColor Green
        exit 0
    }

    Write-Step 'Installing or verifying PyInstaller'
    if (-not $hasPyInstaller) {
        & $Python -m pip install pyinstaller
        if ($LASTEXITCODE -ne 0) {
            throw 'Failed to install PyInstaller'
        }
    }

    Write-Step 'Running compile check'
    & $Python -m compileall -q main.py core gui platforms payment_kit
    if ($LASTEXITCODE -ne 0) {
        throw 'Python compile check failed'
    }

    Write-Step 'Cleaning generated build folders'
    Remove-GeneratedDir -RelativePath 'build' -Root $Root
    Remove-GeneratedDir -RelativePath 'dist' -Root $Root
    New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null
    Remove-GeneratedPath -RelativePath 'release\_internal' -Root $Root -ContinueOnError
    Remove-GeneratedPath -RelativePath 'release\cookies' -Root $Root -ContinueOnError
    Remove-GeneratedPath -RelativePath 'release\downloads' -Root $Root -ContinueOnError
    Remove-GeneratedPath -RelativePath "release\$AppName.exe" -Root $Root -ContinueOnError
    Remove-GeneratedPath -RelativePath "release\$AppName-win64.zip" -Root $Root -ContinueOnError

    Write-Step 'Building folder-based exe'
    & $Python -m PyInstaller `
        --noconfirm `
        --clean `
        --onedir `
        --windowed `
        --name $AppName `
        --exclude-module pytest `
        --collect-all selenium `
        --hidden-import selenium.webdriver.edge.webdriver `
        --hidden-import selenium.webdriver.edge.service `
        --collect-data f2 `
        "$Root\main.py"
    if ($LASTEXITCODE -ne 0) {
        throw 'PyInstaller build failed'
    }

    if (-not (Test-Path -LiteralPath (Join-Path $DistDir "$AppName.exe"))) {
        throw "Build output exe not found: $DistDir\$AppName.exe"
    }

    Write-Step 'Preparing runtime folders'
    New-Item -ItemType Directory -Force -Path (Join-Path $DistDir 'cookies') | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $DistDir 'downloads') | Out-Null
    $paymentClientConfig = Join-Path $Root 'payment_client.json'
    if (Test-Path -LiteralPath $paymentClientConfig) {
        Copy-Item -LiteralPath $paymentClientConfig -Destination (Join-Path $DistDir 'payment_client.json') -Force
    }

    if ($SmokeTest) {
        Write-Step 'Running smoke test'
        $exe = Join-Path $DistDir "$AppName.exe"
        $process = Start-Process -FilePath $exe -PassThru -WindowStyle Hidden
        Start-Sleep -Seconds 6
        if ($process.HasExited) {
            throw "Smoke test failed: exe exited with code $($process.ExitCode)"
        }
        Stop-Process -Id $process.Id -Force
    }

    if (-not $SkipZip) {
        Write-Step 'Creating release zip'
        $zipPath = Join-Path $ReleaseDir "$AppName-win64-$Version.zip"
        $latestZipPath = Join-Path $ReleaseDir "$AppName-win64-latest.zip"

        if (Test-Path -LiteralPath $zipPath) {
            Remove-Item -LiteralPath $zipPath -Force
        }
        if (Test-Path -LiteralPath $latestZipPath) {
            Remove-Item -LiteralPath $latestZipPath -Force
        }

        Compress-Archive -Path $DistDir -DestinationPath $zipPath -CompressionLevel Optimal
        Copy-Item -LiteralPath $zipPath -Destination $latestZipPath -Force

        $zipSize = '{0:N1} MB' -f ((Get-Item -LiteralPath $zipPath).Length / 1MB)
        Write-Host ''
        Write-Host "Release zip: $zipPath"
        Write-Host "Latest zip : $latestZipPath"
        Write-Host "Zip size   : $zipSize"
    }

    Write-Host ''
    Write-Host 'Build completed successfully.' -ForegroundColor Green
    Write-Host "Exe folder : $DistDir"

    if ($OpenReleaseFolder) {
        Start-Process explorer.exe $ReleaseDir
    }
}
catch {
    $exitCode = 1
    Write-Host ''
    Write-Host "Build failed: $($_.Exception.Message)" -ForegroundColor Red
}
finally {
    if ($locationPushed) {
        Pop-Location
    }
    if ($PauseOnExit) {
        Write-Host ''
        Read-Host 'Press Enter to close'
    }
}

exit $exitCode
