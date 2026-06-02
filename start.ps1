$env:QUIP_VALIDATOR_URL = "http://localhost:20049/rpc"
$env:CACHE_DIR = "/tmp/quip-cache"
$env:PORT = "8081"
Start-Process -NoNewWindow -FilePath "python" -ArgumentList "app.py"
Start-Sleep 3
Write-Host "Server should be running on http://localhost:8081"
