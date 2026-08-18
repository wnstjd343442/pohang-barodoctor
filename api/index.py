import sys
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app import app as flask_app

class VercelPathFixMiddleware:
    """
    Vercel Serverless WSGI에서 rewrite로 인해 PATH_INFO가 /api/index.py로 변조되는 문제를 해결.
    실제 요청된 원본 URI(HTTP_X_FORWARDED_URI, REQUEST_URI, RAW_URI)를 확인하여 정확한 라우트로 복원합니다.
    """
    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        path_info = environ.get('PATH_INFO', '')
        
        # 만약 PATH_INFO가 rewrite 목적지인 /api/index.py 또는 /api/index 로 들어온 경우
        if path_info in ['/api/index.py', '/api/index', '/api/index/']:
            # 원본 URL 추출
            raw_uri = (
                environ.get('HTTP_X_FORWARDED_URI') or
                environ.get('REQUEST_URI') or
                environ.get('RAW_URI') or
                environ.get('HTTP_X_VERCEL_PATH') or
                environ.get('HTTP_X_MATCHED_PATH') or
                '/'
            )
            # 쿼리스트링 분리
            clean_path = raw_uri.split('?')[0]
            environ['PATH_INFO'] = clean_path if clean_path else '/'

        return self.wsgi_app(environ, start_response)

app = VercelPathFixMiddleware(flask_app)
