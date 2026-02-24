# --------- CVAT
```cmd
d: && cd D:\_git\codeup-spooner\qwen3-vl-finetuning\cvat
docker compose down
docker compose up -d
docker compose ps # 确保所有container都是up状态

docker logs cvat_server | tail -n 50
docker logs traefik | grep error

# 创建超级用户： admin/a@b.com/a_234Uippb.com
docker exec -it cvat_server bash -ic "python3 ~/manage.py createsuperuser" # django/12345678

curl http://localhost:8080

```

