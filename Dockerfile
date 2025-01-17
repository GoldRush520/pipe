# 使用官方 Python 运行时作为父镜像
FROM python:3.12-slim

# 设置工作目录
WORKDIR /app

# 复制当前目录内容到容器中的 /app 目录
COPY .venv .

# 安装项目依赖
RUN pip install --no-cache-dir -r requirements.txt


# 运行主程序
CMD ["python", "main.py"]