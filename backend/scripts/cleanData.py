# cleanData.py
from cleanvision.imagelab import Imagelab

if __name__ == '__main__':
    # 1. 指向你的图片文件夹（支持子文件夹递归）
    imagelab = Imagelab(data_path="./data/")  # 或 "D:/_git/codeup-spooner/llm-train-learning/data-workshop/data"

    # 2. 自动运行所有检查（或指定部分issue）
    imagelab.find_issues()  # 默认跑全部
    # 或者：imagelab.find_issues(issue_types=["blur", "near_duplicates"])

    # 3. 查看报告（终端 + HTML可视化）
    imagelab.report()              # 打印总结 + 生成 issues.html 文件
    # imagelab.visualize()         # 如果你在 Jupyter Notebook 中运行，可以取消注释
