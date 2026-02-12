# swift
```shell
conda deactivate && conda remove --name qwen3vl-swift --all -y
conda create -n qwen3vl-swift-py3.11 python=3.11.14 -y
conda activate qwen3vl-swift
pip install transformers>=4.57
pip install ms-swift[all]>=4.0 -U
pip install qwen_vl_utils>=0.0.14

set SSL_CERT_FILE=
set REQUESTS_CA_BUNDLE=
swift web-ui --server_name 0.0.0.0 --server_port 7860
curl http://localhos:7860

```



