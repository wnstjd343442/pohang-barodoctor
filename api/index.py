import sys
import os

# root 디렉토리를 path에 추가하여 app.py 및 data 불러오기
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app

# Vercel Serverless WSGI 진입점
app = app
