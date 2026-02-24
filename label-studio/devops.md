
# --------- Label-studio
```cmd
d: && cd D:\_git\codeup\spooner\cosco\routine_inspection-qwen3_vl\label-studio

docker rm -f label-studio
docker run -it -d -p 28080:8080 --name label-studio -v %cd%/data:/label-studio/data heartexlabs/label-studio:ci-plt-983
docker logs -f label-studio

curl http://localhost:28080 
a@b.com/a_234Uippb.com
b@b.com/a_234Uippb.com

```
