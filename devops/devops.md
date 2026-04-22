# Label-studio 官网
- https://labelstud.io/

# 安装
```bash
cmd
docker pull heartexlabs/label-studio:20260421.012345-main-a5c6f37
```

# 运行
```bash
cmd
cd /d D:/_git/codeup-spooner/llm-train-learning/data-workshop
docker rm -f label-studio
docker run -it -p 28080:8080 --name label-studio -v "%CD%"/label-studio/data:/label-studio/data heartexlabs/label-studio:20260421.012345-main-a5c6f37

docker logs -f label-studio
```

# 使用
```bash
curl http://localhost:28080

a@b.com/a_234Uippb.com
b@b.com/a_234Uippb.com
```
