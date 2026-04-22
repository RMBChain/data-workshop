# 后端开发与测试均在 Docker：仅启动 api（热重载），不构建/启动生产 web 镜像。Label Studio 需单独部署，见根目录 README。
# 前端在本机开发：另开终端执行 cd frontend; nvm use; npm install; npm run dev
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

docker build -f devops/swift4.03-cpu.dockerfile -t swift4.03-cpu:latest .
docker compose --env-file devops/compose.env.dev -f docker-compose.yml -f devops/docker-compose.dev.yml up -d --build api

Write-Host ""
Write-Host "后端（容器，热重载）: http://127.0.0.1:8702/docs"
Write-Host "Label Studio: 见 README 单独启动（本仓库 Compose 不再包含该服务）"
Write-Host "前端（本机）: cd frontend; nvm use; npm run dev -> http://127.0.0.1:5173"
