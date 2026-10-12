$ErrorActionPreference = 'Stop'

try {
    $siteDirectory = Split-Path -Parent $PSScriptRoot
    $pythonPath = Join-Path $siteDirectory '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        $pythonPath = Join-Path (Split-Path -Parent $siteDirectory) '.venv\Scripts\python.exe'
    }
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        throw 'Python environment not found. Expected .venv in the site or its parent directory.'
    }

    # Never stop an unknown process or start a second importer on an occupied port.
    $portProbe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 8060)
    try {
        $portProbe.Start()
    } catch {
        throw 'Port 8060 is already in use. Stop the existing server with Ctrl+C in its CMD window, then run this file again.'
    } finally {
        $portProbe.Stop()
    }

    $logDirectory = Join-Path $siteDirectory 'data\server-logs'
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    $runStamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $stdoutPath = Join-Path $logDirectory "$runStamp.stdout.log"
    $stderrPath = Join-Path $logDirectory "$runStamp.stderr.log"
    $serverProcess = Start-Process -FilePath $pythonPath `
        -ArgumentList '-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', '8060' `
        -WorkingDirectory $siteDirectory -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru

    $ready = $false
    for ($attempt = 0; $attempt -lt 15; $attempt++) {
        Start-Sleep -Milliseconds 500
        $serverProcess.Refresh()
        if ($serverProcess.HasExited) {
            throw "Server exited. See log: $stderrPath"
        }
        try {
            $response = Invoke-WebRequest -Uri 'http://127.0.0.1:8060/login' -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -eq 200) {
                $ready = $true
                break
            }
        } catch {
            # Startup may need a few seconds; retry without launching another process.
        }
    }
    if (-not $ready) {
        throw "Server was started but did not become ready yet. Check $stderrPath before retrying."
    }
    Write-Host 'RBE is running in the background: http://127.0.0.1:8060/'
    Write-Host "Logs: $logDirectory"
    Start-Process 'http://127.0.0.1:8060/'
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
