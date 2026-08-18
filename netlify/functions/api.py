import sys
import os

# 현재 디렉토리를 sys.path에 추가하여 app.py 및 data 모듈 임포트 가능하게 설정
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import serverless_wsgi
from app import app

def handler(event, context):
    return serverless_wsgi.handle_request(app, event, context)
