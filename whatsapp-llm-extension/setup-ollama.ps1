param(
    [ValidateSet('llama3.2:3b', 'llama3.2:1b', 'tinyllama')]
    [string]$Model = 'llama3.2:1b',
    [string]$ModelDirectory = (Join-Path $PSScriptRoot 'ollama-models')
)

$ErrorActionPreference = 'Stop'

if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Write-Error 'Ollama is not installed. Install it from https://ollama.com/download and run this script again.'
    exit 1
}

Write-Host 'Allowing Chrome extensions to call the local Ollama server...'
[Environment]::SetEnvironmentVariable('OLLAMA_ORIGINS', 'chrome-extension://*', 'User')
$env:OLLAMA_ORIGINS = 'chrome-extension://*'

$resolvedModelDirectory = [System.IO.Path]::GetFullPath($ModelDirectory)
New-Item -ItemType Directory -Path $resolvedModelDirectory -Force | Out-Null
Write-Host "Storing Ollama models in $resolvedModelDirectory"
[Environment]::SetEnvironmentVariable('OLLAMA_MODELS', $resolvedModelDirectory, 'User')
$env:OLLAMA_MODELS = $resolvedModelDirectory

$running = Get-Process -Name 'ollama', 'ollama app' -ErrorAction SilentlyContinue
if ($running) {
    Write-Host 'Restarting Ollama so the browser-origin setting takes effect...'
    $running | Stop-Process -Force
    Start-Sleep -Seconds 2
}

Write-Host 'Starting Ollama...'
Start-Process -FilePath 'ollama' -ArgumentList 'serve' -WindowStyle Hidden

$ready = $false
for ($attempt = 0; $attempt -lt 15; $attempt++) {
    Start-Sleep -Seconds 1
    try {
        Invoke-RestMethod -Uri 'http://localhost:11434/api/tags' -Method Get | Out-Null
        $ready = $true
        break
    } catch {
        # The server may still be starting.
    }
}

if (-not $ready) {
    Write-Error 'Ollama did not start at http://localhost:11434. Open the Ollama app, then try again.'
    exit 1
}

Write-Host "Downloading $Model (this is a one-time download)..."
& ollama pull $Model
if ($LASTEXITCODE -ne 0) {
    Write-Error "Ollama could not download $Model."
    exit $LASTEXITCODE
}

Write-Host "Ready. The extension can now use $Model locally from $resolvedModelDirectory." -ForegroundColor Green
