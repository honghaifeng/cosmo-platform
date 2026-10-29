# 逆龄观察 · 数据服务（零第三方依赖）
# 构建：docker build -t niling-guancha .
# 运行：docker run -d -p 8080:8080 -v $(pwd)/data:/app/data --name niling niling-guancha
FROM python:3.12-slim

WORKDIR /app
COPY app.py index.html guide.md ./
COPY data/ ./data/

ENV PORT=8080
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s \
  CMD python3 -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/health',timeout=4)" || exit 1

CMD ["python3", "app.py"]
