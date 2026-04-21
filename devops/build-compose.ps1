# 生产全栈（Docker）：swift CPU 基础镜像 + 前端多阶段镜像 + API + Label Studio。
# 后端日常开发请用 run-backend-dev.ps1（本机不跑后端进程）。
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

docker build -f devops/swift4.03-cpu.dockerfile -t swift4.03-cpu:latest .
docker compose up -d --build

Write-Host "UI: http://127.0.0.1:9000"
Write-Host "API: http://127.0.0.1:8000/docs"
Write-Host "Label Studio: http://127.0.0.1:8080"
