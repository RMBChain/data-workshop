# 后端开发与测试均在 Docker：启动 label-studio + api（热重载），不构建/启动生产 web 镜像。
# 前端在本机开发：另开终端执行 cd frontend; nvm use; npm install; npm run dev
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

docker build -f devops/swift4.03-cpu.dockerfile -t swift4.03-cpu:latest .
docker compose -f docker-compose.yml -f devops/docker-compose.dev.yml up -d --build label-studio api

Write-Host ""
Write-Host "后端（容器，热重载）: http://127.0.0.1:8000/docs"
Write-Host "Label Studio: http://127.0.0.1:8080"
Write-Host "前端（本机）: cd frontend; nvm use; npm run dev -> http://127.0.0.1:5173"
