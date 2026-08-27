param([string]$Model = 'llama3.2:1b')

$ErrorActionPreference = 'Stop'
$body = @{
    model = $Model
    messages = @(@{ role = 'user'; content = 'Reply with exactly: ready' })
    stream = $false
    options = @{ num_predict = 10 }
} | ConvertTo-Json -Depth 4

try {
    $response = Invoke-RestMethod -Uri 'http://localhost:11434/api/chat' -Method Post -ContentType 'application/json' -Body $body -TimeoutSec 180
    Write-Host "Ollama replied: $($response.message.content)" -ForegroundColor Green
} catch {
    Write-Error "Ollama test failed: $($_.Exception.Message)"
    exit 1
}
