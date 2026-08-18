FROM python:3.14-slim

# ffmpeg 설치 (음성 입력 STT에서 webm→wav 변환할 때 필요)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render 등 호스팅이 PORT 환경변수를 넣어주므로, 없으면 기본값 5000 사용
ENV PORT=5000
EXPOSE 5000

CMD ["sh", "-c", "gunicorn -w 2 --timeout 120 -b 0.0.0.0:${PORT} app:app"]
