import base64
import glob
import json
import math
import os
import re
import time
import wave
from datetime import datetime, timedelta, timezone
from io import BytesIO

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from pydub import AudioSegment

# .env 파일 로드
load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, template_folder=os.path.join(BASE_DIR, "templates"))

# FFmpeg 경로 자동 탐색 (Windows/Mac 호환)
def _ensure_ffmpeg_on_path():
    localappdata = os.environ.get("LOCALAPPDATA", "")
    if localappdata:
        pattern = os.path.join(localappdata, "Microsoft", "WinGet", "Packages", "*FFmpeg*", "**", "ffmpeg.exe")
        for path in glob.glob(pattern, recursive=True):
            bin_dir = os.path.dirname(path)
            if bin_dir not in os.environ.get("PATH", ""):
                os.environ["PATH"] += os.pathsep + bin_dir
            return

_ensure_ffmpeg_on_path()

def get_all_gemini_api_keys():
    """환경변수에 등록된 모든 Gemini API 키를 순서대로 탐색하여 리스트로 반환 (Failover 지원)"""
    keys = []
    candidates = [
        os.environ.get("GEMINI_API_KEY1", ""),
        os.environ.get("GEMINI_API_KEY_1", ""),
        os.environ.get("GEMINI_API_KEY2", ""),
        os.environ.get("GEMINI_API_KEY_2", ""),
        os.environ.get("GEMINI_API_KEY3", ""),
        os.environ.get("GEMINI_API_KEY_3", ""),
        os.environ.get("GEMINI_API_KEY4", ""),
        os.environ.get("GEMINI_API_KEY_4", ""),
        os.environ.get("GEMINI_API_KEY", ""),
        os.environ.get("GOOGLE_API_KEY", "")
    ]
    seen = set()
    for k in candidates:
        if k and k.strip() and k.strip() not in seen:
            seen.add(k.strip())
            keys.append(k.strip())
            
    for i in range(5, 11):
        k = os.environ.get(f"GEMINI_API_KEY{i}") or os.environ.get(f"GEMINI_API_KEY_{i}")
        if k and k.strip() and k.strip() not in seen:
            seen.add(k.strip())
            keys.append(k.strip())
            
    return keys

DECODING_KEY = os.environ.get("DATA_GO_KR_DECODING_KEY", "")

_gemini_clients_cache = {}

def get_gemini_client(api_key=None):
    """특정 API 키 또는 기본 키에 대한 Gemini 클라이언트 생성 및 캐싱"""
    if not api_key:
        all_keys = get_all_gemini_api_keys()
        if not all_keys:
            return None
        api_key = all_keys[0]
        
    if api_key not in _gemini_clients_cache:
        try:
            from google import genai
            _gemini_clients_cache[api_key] = genai.Client(api_key=api_key)
        except Exception as e:
            print(f"Gemini client init error for key {api_key[:8]}...:", e)
            return None
    return _gemini_clients_cache.get(api_key)

DB_FILE = os.path.join(os.path.dirname(__file__), "data", "pohang_hospitals_db.json")
REPORTS_FILE = os.path.join(os.path.dirname(__file__), "data", "reports.json")

def load_hospitals_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_hospitals_db(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_reports():
    if os.path.exists(REPORTS_FILE):
        with open(REPORTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_reports(data):
    with open(REPORTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def calculate_distance_km(lat1, lon1, lat2, lon2):
    if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
        return None
    try:
        lat1, lon1, lat2, lon2 = float(lat1), float(lon1), float(lat2), float(lon2)
        R = 6371.0
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return round(R * c, 2)
    except Exception:
        return None

def format_distance_and_time(dist_km):
    if dist_km is None:
        return None
    if dist_km < 1.0:
        dist_str = f"{int(dist_km * 1000)}m"
        car_mins = max(1, round((dist_km * 1.25) / 25 * 60))
        walk_mins = max(1, round(dist_km / 4 * 60))
        return f"{dist_str} (차로 {car_mins}분 / 도보 {walk_mins}분)"
    else:
        dist_str = f"{dist_km:.1f}km"
        car_mins = max(2, round((dist_km * 1.25) / 30 * 60))
        walk_mins = round(dist_km / 4 * 60)
        return f"{dist_str} (차로 약 {car_mins}분 / 도보 {walk_mins}분)"

# 포항 주요 생활권/동/읍·면 대표 좌표 (오프라인/빠른 주소 변환용)
POHANG_DISTRICT_CENTROIDS = [
    ("흥해읍 남송리", 36.103, 129.388, "북구"),
    ("흥해읍 초곡리", 36.088, 129.345, "북구"),
    ("흥해읍 이인리", 36.072, 129.341, "북구"),
    ("양덕동", 36.0825, 129.3982, "북구"),
    ("장량동", 36.068, 129.378, "북구"),
    ("두호동", 36.059, 129.375, "북구"),
    ("창포동", 36.063, 129.362, "북구"),
    ("우현동", 36.056, 129.355, "북구"),
    ("중앙동", 36.040, 129.366, "북구"),
    ("죽도동", 36.035, 129.364, "북구"),
    ("용흥동", 36.042, 129.349, "북구"),
    ("상대동", 36.018, 129.355, "남구"),
    ("대도동", 36.015, 129.362, "남구"),
    ("해도동", 36.023, 129.372, "남구"),
    ("대이동", 36.022, 129.336, "남구"),
    ("대잠동", 36.013, 129.348, "남구"),
    ("효자동", 36.008, 129.332, "남구"),
    ("지곡동", 36.019, 129.324, "남구"),
    ("연일읍", 35.992, 129.348, "남구"),
    ("오천읍", 35.968, 129.412, "남구"),
    ("구룡포읍", 35.990, 129.558, "남구")
]

def get_pohang_readable_address(lat, lng):
    """위도/경도 좌표를 공식 역지오코딩 API를 통해 표준 도로명/행정동 주소로 변환 (괄호 제외)"""
    if lat is None or lng is None:
        return "포항시 북구 양덕동"
        
    try:
        lat, lng = float(lat), float(lng)
    except (ValueError, TypeError):
        return "포항시 북구 양덕동"

    # 1. OpenStreetMap Nominatim 정밀 리버스 지오코딩 API 호출 (zoom=18)
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lng}&zoom=18&addressdetails=1"
        res = requests.get(url, headers={"User-Agent": "PohangBaroDoctor/1.0", "Accept-Language": "ko"}, timeout=3.0)
        if res.status_code == 200:
            data = res.json()
            addr = data.get("address") or {}
            city = addr.get("city") or addr.get("county") or addr.get("province") or "포항시"
            borough = addr.get("borough") or addr.get("city_district") or ""
            town = addr.get("town") or addr.get("quarter") or addr.get("suburb") or addr.get("village") or addr.get("neighbourhood") or ""
            road = addr.get("road") or ""
            house_num = addr.get("house_number") or ""
            
            parts = []
            if city and "대한민국" not in city:
                parts.append(city)
            if borough and borough not in parts:
                parts.append(borough)
            if town and town not in parts:
                parts.append(town)
            if road and road not in parts:
                if house_num:
                    parts.append(f"{road} {house_num}")
                else:
                    parts.append(road)
                    
            main_addr = " ".join(parts).strip()
            if main_addr:
                return main_addr
    except Exception as e:
        print("Nominatim API error:", e)

    # 2. 오프라인 / 폴백: 포항 내 가장 가까운 동/읍·면 중심점 계산
    closest_name = "북구 양덕동"
    min_dist = 9999
    for name, c_lat, c_lng, gu in POHANG_DISTRICT_CENTROIDS:
        d = calculate_distance_km(lat, lng, c_lat, c_lng)
        if d is not None and d < min_dist:
            min_dist = d
            closest_name = f"{gu} {name}"
            
    return f"포항시 {closest_name}"

# 증상 카테고리 정의 및 대안 진료과 매핑
SYMPTOM_CATEGORIES = {
    "gastro": {
        "title": "장염 / 급성 복통 / 소화기 질환",
        "keywords": ["배", "복통", "설사", "구토", "속쓰림", "체함", "장염", "미식", "위경련", "식중독", "더부룩", "토", "소화", "소화제", "위염"],
        "primary_depts": ["내과", "소화기내과"],
        "alt_depts": ["가정의학과", "이비인후과", "응급의학과"],
        "advice": "🤢 장염/소화기 증상으로 판단됩니다. 탈수 예방 및 통증 완화를 위해 수액 치료가 가능한 병원을 안내해 드립니다. 휴일이나 야간에는 수액실이 완비된 가정의학과 또는 24시간 응급의료센터를 방문하세요."
    },
    "fever_cold": {
        "title": "감기 / 발열 / 호흡기 질환",
        "keywords": ["열", "고열", "기침", "감기", "몸살", "오한", "콧물", "인후통", "목", "독감", "코로나", "가래", "편도", "이비인후과", "목아파"],
        "primary_depts": ["이비인후과", "내과", "가정의학과"],
        "alt_depts": ["응급의학과", "소아청소년과"],
        "advice": "🤒 발열/호흡기 증상으로 판단됩니다. 발열 환자 대면 진료 및 신속항원검사가 가능한 병의원을 우선 매칭했습니다. (발열 시 진료 제한 병원은 주의 안내)"
    },
    "skin": {
        "title": "피부 질환 / 알레르기 / 두드러기",
        "keywords": ["피부", "두드러기", "가려움", "발진", "아토피", "습진", "대상포진", "알레르기", "뾰루지", "여드름", "점", "레이저", "피부과"],
        "primary_depts": ["피부과"],
        "alt_depts": ["내과", "가정의학과"],
        "advice": "🩹 피부 질환 증상으로 판단됩니다. 미용 시술 위주가 아닌 질환/보험치료 전문 피부과 및 알레르기 항히스타민 수액 처방이 가능한 의원을 추천합니다."
    },
    "orthopedic": {
        "title": "관절 / 척추 / 염좌 / 골절 / 근육통",
        "keywords": [
            "팔", "다리", "손", "발", "손가락", "발가락", "손목", "발목", "어깨", "허리", "무릎", "골반", "등", "갈비뼈",
            "뼈", "관절", "염좌", "골절", "접질", "삐", "담", "근육통", "인대", "통증", "아파", "정형외과", "통증의학과", "도수치료",
            "깁스", "물리치료", "팔아파", "다리아파", "허리아파", "무릎아파", "어깨아파", "발목아파", "손목아파"
        ],
        "primary_depts": ["정형외과", "마취통증의학과", "재활의학과"],
        "alt_depts": ["외과", "가정의학과"],
        "advice": "🦴 근골격계/관절 통증으로 판단됩니다. X-ray 검사 및 물리치료, 도수치료가 가능한 정형외과/통증의학과를 추천합니다."
    },
    "ophthalmology": {
        "title": "안과 질환 / 눈 통증 / 충혈",
        "keywords": ["눈", "충혈", "시력", "안과", "다래끼", "눈물", "뻑뻑", "눈통증", "결막염", "눈아파"],
        "primary_depts": ["안과"],
        "alt_depts": ["내과", "가정의학과"],
        "advice": "👁️ 안과 질환으로 판단됩니다. 정밀 시력/안압 검사 및 안약 처방이 가능한 안과의원을 추천합니다."
    },
    "dental": {
        "title": "치과 질환 / 잇몸 / 치통 / 턱관절",
        "keywords": ["치아", "이빨", "잇몸", "치통", "사랑니", "턱", "스케일링", "치과", "이아파", "이빨아파"],
        "primary_depts": ["치과"],
        "alt_depts": [],
        "advice": "🦷 치과 질환으로 판단됩니다. 발치, 충치 치료 및 턱관절 치료가 가능한 치과의원을 추천합니다."
    },
    "urology": {
        "title": "비뇨기 / 방광염 / 결석 / 요로",
        "keywords": ["소변", "오줌", "방광염", "결석", "요로", "비뇨", "비뇨기과", "비뇨의학과"],
        "primary_depts": ["비뇨의학과", "내과"],
        "alt_depts": ["가정의학과"],
        "advice": "💧 비뇨의학과 질환으로 판단됩니다. 요로결석 쇄석술 및 방광염 소변 검사가 가능한 의원을 추천합니다."
    },
    "neuro": {
        "title": "어지럼증 / 두통 / 신경 질환",
        "keywords": ["어지럼", "저림", "마비", "두통", "편두통", "뇌", "실신", "핑", "어지러", "신경과", "머리아파"],
        "primary_depts": ["신경과", "신경외과", "내과"],
        "alt_depts": ["응급의학과"],
        "advice": "🤕 신경 및 두통 증상으로 판단됩니다. 지속적인 급성 두통이나 심한 어지럼증 시 정밀 진단이 가능한 병원을 추천합니다."
    },
    "cardio_chest": {
        "title": "흉통 / 가슴 답답 / 호흡곤란 / 심혈관",
        "keywords": ["가슴", "흉통", "숨", "숨쉬", "호흡", "심장", "답답", "두근", "맥박", "조여", "찔려", "콕콕", "순환기", "흉부", "가슴아파"],
        "primary_depts": ["내과", "흉부외과"],
        "alt_depts": ["응급의학과", "가정의학과"],
        "advice": "🫀 흉통 및 흉부 이상 증상으로 판단됩니다. 지속적인 흉통이나 호흡곤란 시 정밀 심전도/엑스레이 검사가 가능한 내과 또는 24시간 응급의료센터를 방문하세요."
    },
    "trauma": {
        "title": "외상 / 찢어짐 / 출혈 / 화상",
        "keywords": ["상처", "베인", "화상", "찢어짐", "출혈", "피", "봉합", "꿰매", "외과", "피나", "베였어", "데였어", "다쳤어"],
        "primary_depts": ["외과", "정형외과", "응급의학과"],
        "alt_depts": ["가정의학과"],
        "advice": "🩸 외상 및 상처 치료가 필요합니다. 상처 소독, 봉합 처치 및 화상 치료가 가능한 외과 및 응급의료기관을 추천합니다."
    },
    "pediatric": {
        "title": "소아 / 영유아 질환",
        "keywords": ["아이", "소아", "아기", "신생아", "유아", "어린이", "소아과"],
        "primary_depts": ["소아청소년과", "소아과"],
        "alt_depts": ["이비인후과", "가정의학과", "내과", "응급의학과"],
        "advice": "👶 소아 질환 증상입니다. 소아 전문 진료 의원 및 야간 소아 응급 진료가 가능한 권역응급의료센터를 추천합니다."
    },
    "dermatology": {
        "title": "피부 질환 / 알레르기 / 두드러기 / 가려움",
        "keywords": ["피부", "여드름", "두드러기", "가려움", "가려워", "습진", "아토피", "알레르기", "알러지", "건선", "흉터", "무좀", "대상포진", "피부과", "피부염", "뾰루지", "발진"],
        "primary_depts": ["피부과"],
        "alt_depts": ["가정의학과", "내과"],
        "advice": "🧴 피부 질환으로 판단됩니다. 피부염, 알레르기 처방 및 피부 전문 치료가 가능한 피부과의원을 추천합니다."
    },
    "psychiatry": {
        "title": "정신건강 / 불안 / 불면 / 스트레스",
        "keywords": ["우울", "불안", "불면", "잠이안와", "공황", "스트레스", "정신과", "정신건강의학과", "공황장애", "가슴답답불안"],
        "primary_depts": ["정신건강의학과"],
        "alt_depts": ["가정의학과", "신경과"],
        "advice": "🧠 정신건강의학과 전문 상담 및 약물 처방이 가능한 의원을 추천합니다."
    },
    "obgyn": {
        "title": "산부인과 / 여성 질환",
        "keywords": ["생리", "생리통", "질염", "임신", "여성의원", "산부인과", "부인과", "자궁"],
        "primary_depts": ["산부인과"],
        "alt_depts": ["가정의학과", "내과"],
        "advice": "🌸 여성 질환 및 산부인과 전문 진료가 가능한 의원을 추천합니다."
    }
}

WEEKDAYS_KR = ["월", "화", "수", "목", "금", "토", "일"]
WEEKDAY_NAMES = {"월요일": 0, "화요일": 1, "수요일": 2, "목요일": 3, "금요일": 4, "토요일": 5, "일요일": 6}

def analyze_symptom_with_gemini(text):
    """
    [AI-First 임상 트리아지 엔진]
    등록된 모든 Gemini API 키를 순서대로 시도(Failover)하여 안정적인 분석 보장
    """
    keys = get_all_gemini_api_keys()
    if not keys:
        return None
        
    prompt = f"""너는 대한민국 응급의료 및 1차 진료 임상 트리아지(Triage) AI 전문가야.
사용자가 입력한 자연어 증상이나 병원 탐색 요청을 의학적으로 깊이 있게 분석하고, 가장 적절한 표준 진료과와 긴급도를 판단해줘.

[사용자 입력]
"{text}"

[선택 가능한 표준 진료과 목록]
내과, 소아청소년과, 이비인후과, 정형외과, 마취통증의학과, 신경과, 신경외과, 외과, 피부과, 안과, 치과, 비뇨의학과, 산부인과, 정신건강의학과, 재활의학과, 가정의학과, 응급의학과, 흉부외과, 한방내과

[임상 분석 지침]
1. 환자가 호소하는 증상의 의학적 기전과 원인을 추론하여 1순위 추천 진료과(primary_depts, 1~3개)와 대안/응급 진료과(alt_depts, 1~2개)를 결정해.
   - 예) "피부에 두드러기 나고 가려워" -> primary_depts: ["피부과"], alt_depts: ["가정의학과", "내과"]
   - 예) "어깨가 뻐근하고 팔이 저려" -> primary_depts: ["정형외과", "마취통증의학과", "신경외과"], alt_depts: ["재활의학과", "가정의학과"]
   - 예) "장염 걸렸어 수액 맞고 싶어" -> primary_depts: ["내과", "가정의학과"], alt_depts: ["응급의학과"]
   - 예) "애기가 밤에 열이 39도야" -> primary_depts: ["소아청소년과"], alt_depts: ["응급의학과", "이비인후과"]
   - 예) "발목 삐끗해서 붓고 아파" -> primary_depts: ["정형외과", "마취통증의학과"], alt_depts: ["외과", "재활의학과"]
   - 예) "눈이 충혈되고 뻑뻑해" -> primary_depts: ["안과"], alt_depts: ["내과", "가정의학과"]
   - 예) "치통이 심하고 잇몸이 부었어" -> primary_depts: ["치과"], alt_depts: []
2. 시공간 및 긴급도 추출:
   - is_open_now: 지금, 현재, 지금 문 연, 지금 갈 수 있는 등 현재 실시간 진료 가능한 병원을 찾으면 true
   - is_saturday: 토요일 진료 필요 시 true
   - is_sunday: 일요일 진료 필요 시 true
   - is_holiday: 공휴일/빨간날/명절/휴일 진료 필요 시 true
   - is_night: 야간, 밤, 저녁, 새벽, 늦게, 5시/6시 이후 등 야간 진료가 필요하면 true
   - is_emergency: 호흡곤란, 극심한 흉통, 대량 출혈 등 24시 응급실 직행 필요 시 true
   - target_district: 포항 지역명(양덕동, 장성동, 장량동, 이동, 효자동, 두호동, 창포동, 흥해읍, 오천읍 등)이 언급되었으면 해당 동 이름, 없으면 null
3. category_title: 간결하고 전문적인 의학적 증상 요약 제목 (예: "급성 두드러기 및 접촉성 피부염", "경추부 신경통 및 어깨 근막통증증후군")
4. advice: 환자가 즉시 실천할 수 있는 1~2문장의 전문적 의학 대처 요령

반드시 아래 JSON 포맷으로만 출력해 (설명이나 마크다운 백틱 제외):
{{
  "is_medical": true,
  "category_title": "증상 요약 제목",
  "primary_depts": ["진료과1", "진료과2"],
  "alt_depts": ["대안진료과1"],
  "is_open_now": false,
  "is_saturday": false,
  "is_sunday": false,
  "is_holiday": false,
  "is_night": false,
  "is_emergency": false,
  "target_district": null,
  "urgency": "routine",
  "advice": "환자 대처 조언"
}}

단, 사용자의 입력이 완전히 의학과 무관한 단순 인사/장난(예: '안녕', '반가워', '바보')인 경우에만:
{{
  "is_medical": false,
  "category_title": "일반 대화",
  "primary_depts": [],
  "alt_depts": [],
  "is_open_now": false,
  "is_saturday": false,
  "is_sunday": false,
  "is_holiday": false,
  "is_night": false,
  "is_emergency": false,
  "target_district": null,
  "urgency": "routine",
  "advice": ""
}}"""

    for idx, key in enumerate(keys, 1):
        client = get_gemini_client(key)
        if not client:
            continue
        try:
            res = client.models.generate_content(
                model="gemini-3.6-flash",
                contents=prompt
            )
            raw_text = (res.text or "").strip()
            raw_text = re.sub(r"^```json\s*", "", raw_text)
            raw_text = re.sub(r"^```\s*", "", raw_text)
            raw_text = re.sub(r"\s*```$", "", raw_text).strip()
            data = json.loads(raw_text)
            return data
        except Exception as e:
            print(f"[Gemini Key #{idx} Failover] symptom analysis error: {e}")
            continue
            
    return None

def analyze_symptom_and_intent(text):
    text_lower = text.lower().strip()
    
    # 1. 명백한 비의료 단순 인사/장난 사전 필터링
    is_pure_non_medical = text_lower in ["바보", "안녕", "안녕하세요", "ㅎㅇ", "하이", "ㅋㅋ", "ㅎㅎ", "테스트", "test"]
    if is_pure_non_medical:
        return {
            "category_key": "non_medical",
            "is_medical_symptom": False,
            "category_title": "일반 대화",
            "primary_depts": [],
            "alt_depts": [],
            "advice": "",
            "target_date_str": "오늘",
            "is_saturday": (datetime.now().weekday() == 5),
            "is_sunday": (datetime.now().weekday() == 6),
            "is_holiday": False,
            "is_night": False,
            "target_district": None
        }

    # 2. 날짜 및 시간 키워드 파싱
    today = datetime.now().date()
    is_open_now = any(k in text for k in ["지금", "현재", "문 연", "문연", "문 연곳", "문연곳", "진료중", "지금갈", "갈수있는", "갈 수 있는"])
    is_saturday = False
    is_sunday = False
    is_holiday = False
    is_night = False
    target_date_str = "오늘"
    
    if "모레" in text:
        d = today + timedelta(days=2)
        target_date_str = f"{d.month}월 {d.day}일({WEEKDAYS_KR[d.weekday()]})"
        is_saturday = (d.weekday() == 5)
        is_sunday = (d.weekday() == 6)
    elif "내일" in text:
        d = today + timedelta(days=1)
        target_date_str = f"{d.month}월 {d.day}일({WEEKDAYS_KR[d.weekday()]})"
        is_saturday = (d.weekday() == 5)
        is_sunday = (d.weekday() == 6)
    elif "오늘" in text or "지금" in text:
        d = today
        target_date_str = f"오늘 {d.month}월 {d.day}일({WEEKDAYS_KR[d.weekday()]})"
        is_saturday = (d.weekday() == 5)
        is_sunday = (d.weekday() == 6)
    else:
        d = None
        for name, target_weekday in WEEKDAY_NAMES.items():
            if name in text:
                diff = (target_weekday - today.weekday()) % 7
                if diff == 0 and ("다음주" in text or "다음" in text):
                    diff = 7
                d = today + timedelta(days=diff)
                target_date_str = f"{name}({d.month}월 {d.day}일)"
                is_saturday = (target_weekday == 5)
                is_sunday = (target_weekday == 6)
                break
        if d is None:
            m = re.search(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", text)
            if m:
                month, day = int(m.group(1)), int(m.group(2))
                try:
                    d = datetime(today.year, month, day).date()
                    target_date_str = f"{month}월 {day}일({WEEKDAYS_KR[d.weekday()]})"
                    is_saturday = (d.weekday() == 5)
                    is_sunday = (d.weekday() == 6)
                except ValueError:
                    d = today
                    target_date_str = "오늘"
            else:
                target_date_str = "오늘"
                is_saturday = (today.weekday() == 5)
                is_sunday = (today.weekday() == 6)
                
    if any(k in text for k in ["토요일", "토욜", "토요"]):
        is_saturday = True
    if any(k in text for k in ["일요일", "일욜", "일요"]):
        is_sunday = True
    if any(k in text for k in ["공휴일", "휴일", "빨간날", "명절", "설날", "추석", "광복절", "삼일절", "어린이날", "개천절", "한글날", "크리스마스", "성탄절", "신정"]):
        is_holiday = True
    if any(k in text for k in ["야간", "밤", "저녁", "새벽", "늦게", "5시", "6시", "7시", "8시", "9시", "24시"]):
        is_night = True
        
    target_district = None
    district_map = {
        "양덕": "양덕동", "장량": "장량동", "장성": "장량동", "두호": "두호동",
        "창포": "창포동", "한동대": "흥해읍/한동대", "흥해": "흥해읍/한동대",
        "초곡": "흥해읍/한동대", "우현": "우현동", "이동": "이동", "효자": "효자동",
        "지곡": "지곡동", "대이동": "대이동", "오천": "오천읍", "문덕": "오천읍"
    }
    for kw, dist_name in district_map.items():
        if kw in text:
            target_district = dist_name
            break

    # 3. [AI-First Triage] Gemini 3.6 Flash를 1순위로 실행하여 임상 증상 및 진료과 정밀 분석
    ai_result = analyze_symptom_with_gemini(text)
    if ai_result:
        if not ai_result.get("is_medical", True):
            return {
                "category_key": "non_medical",
                "is_medical_symptom": False,
                "category_title": ai_result.get("category_title", "일반 대화"),
                "primary_depts": [],
                "alt_depts": [],
                "advice": "",
                "target_date_str": target_date_str,
                "is_open_now": is_open_now,
                "is_saturday": is_saturday,
                "is_sunday": is_sunday,
                "is_holiday": is_holiday,
                "is_night": is_night,
                "target_district": target_district
            }
        
        primary_depts = ai_result.get("primary_depts") or ["내과", "가정의학과"]
        alt_depts = ai_result.get("alt_depts") or ["응급의학과"]
        if ai_result.get("is_open_now"):
            is_open_now = True
        if ai_result.get("is_saturday"):
            is_saturday = True
        if ai_result.get("is_sunday"):
            is_sunday = True
        if ai_result.get("is_holiday"):
            is_holiday = True
        if ai_result.get("is_night"):
            is_night = True
        if ai_result.get("target_district"):
            target_district = target_district or ai_result.get("target_district")

        return {
            "category_key": "ai_triage",
            "is_medical_symptom": True,
            "category_title": ai_result.get("category_title", "의료 진료 안내"),
            "primary_depts": primary_depts,
            "alt_depts": alt_depts,
            "advice": ai_result.get("advice", "가까운 진료 가능 병의원을 안내합니다."),
            "target_date_str": target_date_str,
            "is_open_now": is_open_now,
            "is_saturday": is_saturday,
            "is_sunday": is_sunday,
            "is_holiday": is_holiday,
            "is_night": is_night,
            "target_district": target_district,
            "urgency": ai_result.get("urgency", "routine")
        }

    # 4. [Fallback] 만약 Gemini 일시 오류 시 로컬 키워드 사전으로 안전하게 폴백
    matched_cat_key = None
    max_score = 0
    for cat_key, cat_data in SYMPTOM_CATEGORIES.items():
        score = 0
        for kw in cat_data["keywords"]:
            if kw in text_lower:
                score += (3 if len(kw) >= 2 else 1)
        if score > max_score:
            max_score = score
            matched_cat_key = cat_key

    if matched_cat_key:
        cat_info = SYMPTOM_CATEGORIES[matched_cat_key]
        return {
            "category_key": matched_cat_key,
            "is_medical_symptom": True,
            "category_title": cat_info["title"],
            "primary_depts": cat_info["primary_depts"],
            "alt_depts": cat_info["alt_depts"],
            "advice": cat_info["advice"],
            "target_date_str": target_date_str,
            "is_open_now": is_open_now,
            "is_saturday": is_saturday,
            "is_sunday": is_sunday,
            "is_holiday": is_holiday,
            "is_night": is_night,
            "target_district": target_district
        }

    # 일반 통증 호소 폴백
    pain_words = ["아파", "통증", "쑤셔", "결려", "이상해", "불편", "괴로워", "저려", "따가워", "욱신", "부었", "다쳤", "쓰려", "답답", "병원", "진료"]
    if any(pw in text_lower for pw in pain_words):
        return {
            "category_key": "general_pain",
            "is_medical_symptom": True,
            "category_title": "신체 통증 및 1차 진료 안내",
            "primary_depts": ["내과", "가정의학과", "정형외과"],
            "alt_depts": ["응급의학과"],
            "advice": "신체 불편 증상에 대해 1차 진료가 가능한 가까운 내과/정형외과/가정의학과를 안내합니다.",
            "target_date_str": target_date_str,
            "is_open_now": is_open_now,
            "is_saturday": is_saturday,
            "is_sunday": is_sunday,
            "is_holiday": is_holiday,
            "is_night": is_night,
            "target_district": target_district
        }

    return {
        "category_key": "general_hospital",
        "is_medical_symptom": True,
        "category_title": "가까운 진료 가능 병의원",
        "primary_depts": ["내과", "가정의학과", "이비인후과"],
        "alt_depts": ["응급의학과"],
        "advice": "내 위치 기준 진료 가능한 가까운 포항 병의원을 안내합니다.",
        "target_date_str": target_date_str,
        "is_open_now": is_open_now,
        "is_saturday": is_saturday,
        "is_sunday": is_sunday,
        "is_holiday": is_holiday,
        "is_night": is_night,
        "target_district": target_district
    }

def is_hospital_open_now(h, now_dt=None):
    """한국 시간(KST) 기준 현재 시각에 진료 중인지 판별"""
    if h.get("is_emergency"):
        return True
    if not now_dt:
        now_dt = datetime.now(timezone(timedelta(hours=9)))
        
    day_keys = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    day_key = day_keys[now_dt.weekday()]
    hours = h.get("hours", {})
    h_str = hours.get(day_key)
    if not h_str or "휴" in str(h_str):
        return False
    try:
        start_s, end_s = str(h_str).split("~")
        sh, sm = map(int, start_s.strip().split(":"))
        eh, em = map(int, end_s.strip().split(":"))
        curr_m = now_dt.hour * 60 + now_dt.minute
        return (sh * 60 + sm <= curr_m < eh * 60 + em)
    except Exception:
        return False

def get_hospital_open_status_kr(h, now_dt=None):
    """한국 시간(KST) 기준 현재 진료 상태 텍스트 반환"""
    if h.get("is_emergency"):
        return {"is_open": True, "label": "🚨 24시 응급진료", "type": "er"}
    if not now_dt:
        now_dt = datetime.now(timezone(timedelta(hours=9)))
        
    day_keys = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    day_key = day_keys[now_dt.weekday()]
    hours = h.get("hours", {})
    h_str = hours.get(day_key)
    if not h_str or "휴" in str(h_str):
        return {"is_open": False, "label": "🔴 오늘 휴진", "type": "closed"}
    try:
        start_s, end_s = str(h_str).split("~")
        sh, sm = map(int, start_s.strip().split(":"))
        eh, em = map(int, end_s.strip().split(":"))
        curr_m = now_dt.hour * 60 + now_dt.minute
        if sh * 60 + sm <= curr_m < eh * 60 + em:
            return {"is_open": True, "label": f"🟢 현재 진료중 (~{end_s.strip()})", "type": "open"}
        else:
            return {"is_open": False, "label": f"🔴 진료종료 ({h_str})", "type": "closed"}
    except Exception:
        return {"is_open": False, "label": f"⏰ 진료시간: {h.get('hours_summary', '문의')}", "type": "unknown"}

def rank_and_filter_hospitals(hospitals, analysis, user_lat=None, user_lng=None, sort_by="recommend"):
    if analysis.get("category_key") == "non_medical":
        return []
        
    primary_depts = set(analysis.get("primary_depts") or [])
    alt_depts = set(analysis.get("alt_depts") or [])
    is_open_now = analysis.get("is_open_now", False)
    is_saturday = analysis.get("is_saturday", False)
    is_sunday = analysis.get("is_sunday", False)
    is_holiday = analysis.get("is_holiday", False)
    is_night = analysis.get("is_night", False)
    target_district = analysis.get("target_district")
    is_medical = analysis.get("is_medical_symptom", True)
    
    scored_list = []
    
    for h in hospitals:
        if not h.get("is_open_today", True):
            continue
            
        h_depts = set(h.get("departments") or [])
        common_primary = h_depts.intersection(primary_depts)
        common_alt = h_depts.intersection(alt_depts)
        is_er = bool(h.get("is_emergency"))
        
        if is_medical and (primary_depts or alt_depts):
            if not common_primary and not common_alt and not is_er:
                continue

        score = 0
        match_reasons = []
        
        if common_primary:
            score += 70
            match_reasons.append(f"추천 진료과: {', '.join(common_primary)}")
        elif common_alt:
            score += 45
            match_reasons.append(f"대안 진료과: {', '.join(common_alt)}")
        elif is_er:
            score += 35
            match_reasons.append("24시간 응급진료")
        else:
            score += 5
            
        # 현재 진료중 가중치
        h_open_now = is_hospital_open_now(h)
        if is_open_now:
            if h_open_now:
                score += 75
                match_reasons.append("현재 진료중")
            else:
                score -= 60
                
        if is_saturday:
            if h.get("saturday_open") or is_er:
                score += 45
                match_reasons.append("토요일 진료 가능")
            else:
                score -= 35
                
        if is_sunday:
            if h.get("sunday_open") or is_er:
                score += 50
                match_reasons.append("일요일 진료 가능")
            else:
                score -= 40
                
        if is_night:
            if h.get("night_open") or is_er:
                score += 40
                match_reasons.append("야간/24시간 운영")
            else:
                score -= 30
                
        if target_district:
            if target_district in (h.get("district") or "") or target_district in (h.get("address") or ""):
                score += 35
                match_reasons.append(f"{target_district} 생활권")
        else:
            if "양덕동" in (h.get("district") or "") or "장량동" in (h.get("district") or ""):
                score += 10
                
        dist_km = None
        dist_text = None
        if user_lat is not None and user_lng is not None and h.get("lat") and h.get("lng"):
            dist_km = calculate_distance_km(user_lat, user_lng, h["lat"], h["lng"])
            dist_text = format_distance_and_time(dist_km)
            
            if dist_km is not None:
                if dist_km <= 1.5:
                    score += 30
                    match_reasons.append("반경 1.5km 이내")
                elif dist_km <= 3.0:
                    score += 20
                elif dist_km <= 6.0:
                    score += 10
                else:
                    score -= min(25, int(dist_km * 1.5))
                    
        if h.get("posaka") == "O":
            score += 5
            
        query = h.get("naver_search_query") or f"{h['name']} 포항"
        naver_map_url = f"https://map.naver.com/p/search/{requests.utils.quote(query)}"
        kakao_map_url = f"https://map.kakao.com/link/to/{requests.utils.quote(h['name'])},{h.get('lat')},{h.get('lng')}"
        
        open_status = get_hospital_open_status_kr(h)
        scored_list.append({
            **h,
            "match_score": score,
            "match_reasons": match_reasons,
            "distance_km": dist_km,
            "distance_text": dist_text,
            "naver_map_url": naver_map_url,
            "kakao_map_url": kakao_map_url,
            "is_open_now": open_status["is_open"],
            "open_status_label": open_status["label"],
            "open_status_type": open_status["type"]
        })
        
    if sort_by == "distance" and user_lat is not None:
        scored_list.sort(key=lambda x: (x.get("distance_km") is None, x.get("distance_km") or 999, -x["match_score"]))
    else:
        scored_list.sort(key=lambda x: x["match_score"], reverse=True)
        
    # 만약 해당 특수 진료과로 진료 가능한 개원의가 현재 없는 경우 24시 응급의료센터로만 안전하게 안내
    if not scored_list:
        er_hospitals = [h for h in hospitals if h.get("is_emergency")]
        scored_list = er_hospitals if er_hospitals else hospitals[:5]
        
    return scored_list

def generate_gemini_conversational_reply(user_message, analysis, top_hospitals):
    """Gemini 3.6 Flash를 활용해 실제 따뜻하고 전문적인 의료 대화 답변 생성 (다중 API 키 Failover 지원)"""
    keys = get_all_gemini_api_keys()
    if not keys:
        return None, "Gemini API 키가 설정되지 않았습니다."
        
    if analysis.get("category_key") == "non_medical":
        prompt = f"""너는 포항 시민과 학생들을 위한 AI 의료 안내 비서 "포항 바로닥터"야.
사용자가 "{user_message}"라고 입력했어.
상투적인 "안녕하세요" 첫인사를 반복하지 말고, 사용자의 말에 짧고 자연스럽게 한 문장으로 반응한 뒤 "어디가 불편하시거나 찾으시는 병원이 있으신가요? (예: 배 아파, 목감기, 일요일 정형외과)"라고 1~2문장으로 간결하게 물어봐줘."""
        for idx, key in enumerate(keys, 1):
            client = get_gemini_client(key)
            if not client:
                continue
            try:
                res = client.models.generate_content(model="gemini-3.6-flash", contents=prompt)
                return (res.text or "").strip(), None
            except Exception as e:
                print(f"[Gemini Key #{idx} Failover] non_medical reply error: {e}")
                continue
        return None, "모든 AI API 키 할당량 초과"
        
    hospital_summary = []
    for h in top_hospitals[:4]:
        er_badge = "[24시 응급실]" if h.get("is_emergency") else ""
        hospital_summary.append(f"- {h['name']} ({h.get('district', '')}): {h.get('hours_summary', '')} {er_badge} 특징: {', '.join(h.get('features', [])[:2])}")
        
    hosp_text = "\n".join(hospital_summary) if hospital_summary else "진료 가능 병원"
    
    prompt = f"""너는 포항 시민과 학생들을 위한 따뜻하고 전문적인 AI 의료 트리아지 닥터 "포항 바로닥터"야.

[환자 호소 증상]
"{user_message}"

[의학적 트리아지 분석 결과]
- 추정 질환/상태: {analysis.get('category_title', '증상 호소')}
- 권장 1차 진료과: {', '.join(analysis.get('primary_depts', []))} (대안/응급: {', '.join(analysis.get('alt_depts', []))})
- 진료 희망 일시: {analysis.get('target_date_str', '오늘')}
- 1차 대처 조언: {analysis.get('advice', '')}

[포항 관내 진료 가능 추천 병원 (공공데이터 663개 연동 기반)]
{hosp_text}

[답변 작성 가이드]
1. 불필요한 형식적 첫인사("안녕하세요")는 생략하고, 환자가 겪고 있는 고통/불편에 대한 따뜻한 공감으로 바로 시작해.
2. 왜 이 진료과({', '.join(analysis.get('primary_depts', []))})를 방문해야 하는지 환자의 눈높이에 맞게 1문장으로 친절히 설명해줘.
3. 환자가 진료 전/병원 이동 중 취해야 할 행동 요령(예: 탈수 예방을 위한 소량의 미온수/수액 권고, RICE 요법, 체온 관리, 금식 여부 등)을 명확하게 짚어줘.
4. 아래 추천 병원 목록 중에서 적절한 병원 1~2곳을 자연스럽게 언급하며 진료시간 확인 및 방문을 권유해줘.
5. 모바일 화면에서 빠르게 읽을 수 있도록 읽기 쉬운 한국어 대화체(3~4문단, 200~250자 내외)로 작성하고 마크다운 볼드(**강조**)를 적절히 사용해줘."""

    last_err = None
    for idx, key in enumerate(keys, 1):
        client = get_gemini_client(key)
        if not client:
            continue
        try:
            res = client.models.generate_content(
                model="gemini-3.6-flash",
                contents=prompt
            )
            return (res.text or "").strip(), None
        except Exception as e:
            last_err = e
            print(f"[Gemini Key #{idx} Failover] reply error: {e}")
            continue
            
    err_str = str(last_err) if last_err else "API Key Quota Exceeded"
    if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
        err_msg = "⚠️ Gemini API 모든 무료 사용량 한도에 도달했습니다."
    else:
        err_msg = f"⚠️ Gemini API 오류: {err_str[:80]}"
    return None, err_msg

@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, PUT, DELETE"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return response

@app.route("/", methods=["GET", "POST", "OPTIONS"])
@app.route("/index.html", methods=["GET"])
def index():
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"}), 200
    if request.method == "POST":
        payload = request.get_json(force=True, silent=True) or {}
        if "report_type" in payload:
            return api_report()
        elif "action" in payload:
            return api_admin_toggle()
        else:
            return api_chat()
    return render_template("index.html")

@app.route("/api/chat", methods=["GET", "POST", "OPTIONS"])
@app.route("/chat", methods=["GET", "POST", "OPTIONS"])
def api_chat():
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"}), 200
        
    payload = request.get_json(force=True, silent=True) or {}
    message = (payload.get("message") or "").strip()
    user_lat = payload.get("lat")
    user_lng = payload.get("lng")
    sort_by = payload.get("sort_by", "recommend")

    if any(kw in message for kw in ["가까운", "거리순", "가장 가까운", "가까이", "근처"]):
        sort_by = "distance"

    if not message:
        return jsonify({"error": "증상이나 상황을 입력해주세요. (예: 일요일 5시에 장염 걸렸어)"}), 400

    hospitals = load_hospitals_db()
    analysis = analyze_symptom_and_intent(message)
    recommended = rank_and_filter_hospitals(hospitals, analysis, user_lat, user_lng, sort_by)
    
    # 상위 추천 병원들
    top_hospitals = recommended[:15]

    # 실제 Gemini 3.6 Flash 대화형 응답 생성 시도
    ai_reply, ai_error = generate_gemini_conversational_reply(message, analysis, top_hospitals)
    
    if not ai_reply:
        if not top_hospitals:
            return jsonify({
                "status": "error",
                "error": "⚠️ 현재 AI 사용량이 초과되었습니다. 잠시 후 다시 시도해 주세요.",
                "hospitals": []
            }), 200

        # Gemini 호출이 실패해도(할당량 초과 / 키 만료 등) 솔직한 안내와 함께 공공데이터 추천 결과 제공
        primary_str = ", ".join(analysis.get("primary_depts", [])) if analysis.get("primary_depts") else "일반 진료"
        alt_str = ", ".join(analysis.get("alt_depts", []))
        loc_guide = " (📍 내 위치 기준)" if user_lat else ""

        reply_lines = [
            "⚠️ **현재 AI 접속량 초과(API 한도 도달)로 인해 답변 생성이 일시 지연되고 있습니다. 잠시 후 다시 시도해 주세요.**",
            "*(※ 공공데이터 기준 맞춤 병원 및 실시간 진료시간은 아래 목록에서 바로 확인하실 수 있습니다.)*",
            "",
            f"🔍 **추천 진료과**: 1순위 `{primary_str}`" + (f" (대안: `{alt_str}`)" if alt_str else ""),
            f"📅 **진료 기준**: {analysis.get('target_date_str', '오늘')}{loc_guide}",
            "",
            analysis.get("advice", "진료시간을 확인하시고 아래 지도 길찾기 또는 전화 문의 후 방문하세요.")
        ]
        ai_reply = "\n".join(reply_lines)
    
    return jsonify({
        "status": "success",
        "analysis": analysis,
        "reply": ai_reply,
        "hospitals": top_hospitals,
        "total_matched": len(recommended),
        "user_has_location": bool(user_lat and user_lng),
        "public_data_active": True,
        "is_ai_generated": bool(not ai_error and ai_reply)
    })

@app.route("/api/reverse-geocode", methods=["GET", "POST", "OPTIONS"])
@app.route("/reverse-geocode", methods=["GET", "POST", "OPTIONS"])
def api_reverse_geocode():
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"}), 200
    lat = request.args.get("lat")
    lng = request.args.get("lng")
    if not lat or not lng:
        payload = request.get_json(force=True, silent=True) or {}
        lat = payload.get("lat")
        lng = payload.get("lng")
    address = get_pohang_readable_address(lat, lng)
    return jsonify({
        "status": "success",
        "address": address,
        "lat": lat,
        "lng": lng
    })

@app.route("/api/hospitals", methods=["GET"])
@app.route("/hospitals", methods=["GET"])
def api_hospitals():
    district = request.args.get("district", "").strip()
    tag = request.args.get("tag", "").strip()
    open_now_only = request.args.get("open_now", "").lower() == "true"
    saturday_only = request.args.get("saturday", "").lower() == "true"
    sunday_only = request.args.get("sunday", "").lower() == "true"
    holiday_only = request.args.get("holiday", "").lower() == "true"
    night_only = request.args.get("night", "").lower() == "true"
    er_only = request.args.get("er", "").lower() == "true"
    posaka_only = request.args.get("posaka", "").lower() == "true"
    user_lat = request.args.get("lat")
    user_lng = request.args.get("lng")
    sort_by = request.args.get("sort_by", "recommend")
    
    hospitals = load_hospitals_db()
    filtered = []
    
    for h in hospitals:
        if district and district not in (h.get("district") or "") and district not in (h.get("address") or ""):
            continue
        if open_now_only and not is_hospital_open_now(h):
            continue
        if saturday_only and not (h.get("saturday_open") or h.get("is_emergency")):
            continue
        if sunday_only and not (h.get("sunday_open") or h.get("is_emergency")):
            continue
        if holiday_only and not (h.get("holiday_open") or h.get("is_emergency")):
            continue
        if night_only and not (h.get("night_open") or h.get("is_emergency")):
            continue
        if er_only and not h.get("is_emergency"):
            continue
        if posaka_only and h.get("posaka") != "O":
            continue
        if tag and tag not in (h.get("features") or []) and tag not in (h.get("departments") or []):
            continue
            
        dist_km = None
        dist_text = None
        if user_lat and user_lng and h.get("lat") and h.get("lng"):
            dist_km = calculate_distance_km(user_lat, user_lng, h["lat"], h["lng"])
            dist_text = format_distance_and_time(dist_km)
            
        query = h.get("naver_search_query") or f"{h['name']} 포항"
        h["naver_map_url"] = f"https://map.naver.com/p/search/{requests.utils.quote(query)}"
        h["kakao_map_url"] = f"https://map.kakao.com/link/to/{requests.utils.quote(h['name'])},{h.get('lat')},{h.get('lng')}"
        h["distance_km"] = dist_km
        h["distance_text"] = dist_text
        filtered.append(h)
        
    if sort_by == "distance" and user_lat:
        filtered.sort(key=lambda x: (x.get("distance_km") is None, x.get("distance_km") or 999))
        
    return jsonify({"count": len(filtered), "hospitals": filtered})

@app.route("/api/report", methods=["GET", "POST", "OPTIONS"])
@app.route("/report", methods=["GET", "POST", "OPTIONS"])
def api_report():
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"}), 200
        
    payload = request.get_json(force=True, silent=True) or {}
    hospital_id = payload.get("hospital_id", "").strip()
    hospital_name = payload.get("hospital_name", "").strip()
    report_type = payload.get("report_type", "기타 정보 제보").strip()
    content = payload.get("content", "").strip()
    author = payload.get("author", "포항 시민/학생").strip()

    if not content and not report_type:
        return jsonify({"error": "제보 내용을 입력해주세요."}), 400

    new_report = {
        "id": f"rep-{int(time.time()*1000)}",
        "hospital_id": hospital_id,
        "hospital_name": hospital_name,
        "report_type": report_type,
        "content": content,
        "author": author,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    reports = load_reports()
    reports.insert(0, new_report)
    save_reports(reports)

    return jsonify({
        "status": "success",
        "message": "소중한 제보 감사합니다! 검토 후 실시간 정보에 반영됩니다.",
        "report": new_report
    })

@app.route("/api/reports", methods=["GET"])
@app.route("/reports", methods=["GET"])
def api_get_reports():
    reports = load_reports()
    return jsonify({"reports": reports})

@app.route("/api/admin/toggle", methods=["GET", "POST", "OPTIONS"])
@app.route("/admin/toggle", methods=["GET", "POST", "OPTIONS"])
def api_admin_toggle():
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"}), 200
    payload = request.get_json(force=True, silent=True) or {}
    hospital_id = payload.get("hospital_id")
    action = payload.get("action")

    hospitals = load_hospitals_db()
    target = None
    for h in hospitals:
        if h["id"] == hospital_id:
            target = h
            break

    if not target:
        return jsonify({"error": "해당 병원을 찾을 수 없습니다."}), 404

    if action == "toggle_open":
        target["is_open_today"] = not target.get("is_open_today", True)
    elif action == "toggle_fever":
        current = target.get("fever_status", "possible")
        if current == "possible":
            target["fever_status"] = "caution"
            target["fever_badge"] = "⚠️ 37.5도 이상 사전문의"
        elif current == "caution":
            target["fever_status"] = "impossible"
            target["fever_badge"] = "⛔ 발열 시 진료 불가"
        else:
            target["fever_status"] = "possible"
            target["fever_badge"] = "🔥 발열 진료 가능"
    elif action == "toggle_posaka":
        current = target.get("posaka", "O")
        target["posaka"] = "X" if current == "O" else "O"

    save_hospitals_db(hospitals)
    return jsonify({"status": "success", "hospital": target})

@app.route("/api/stt", methods=["POST"])
@app.route("/stt", methods=["POST"])
def api_stt():
    audio_file = request.files.get("audio")
    if not audio_file:
        return jsonify({"error": "audio 파일이 없습니다."}), 400

    client = get_gemini_client()
    if not client:
        return jsonify({"error": "Gemini API 키가 설정되지 않았습니다. 브라우저 음성 인식을 이용하세요."}), 400

    try:
        sound = AudioSegment.from_file(BytesIO(audio_file.read()))
        wav_buf = BytesIO()
        sound.export(wav_buf, format="wav")
        wav_bytes = wav_buf.getvalue()
        audio_b64 = base64.b64encode(wav_bytes).decode("ascii")

        interaction = client.interactions.create(
            model="gemini-3.6-flash",
            input=[
                {"type": "text", "text": "다음 음성을 한국어 텍스트로 정확하게 변환해줘. 부가 설명 없이 인식된 텍스트만 출력해."},
                {"type": "audio", "data": audio_b64, "mime_type": "audio/wav"},
            ],
        )
        text = (interaction.output_text or "").strip()
        return jsonify({"text": text})
    except Exception as e:
        return jsonify({"error": f"음성 변환 실패: {e}"}), 500

@app.route("/api/tts", methods=["POST"])
@app.route("/tts", methods=["POST"])
def api_tts():
    payload = request.get_json(force=True, silent=True) or {}
    text = (payload.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text가 비어있습니다."}), 400

    client = get_gemini_client()
    if not client:
        return jsonify({"error": "Gemini API 키가 설정되지 않았습니다."}), 400

    try:
        speak_input = f"다음 문장을 한국어로 자연스럽게 읽어줘: {text}"
        interaction = client.interactions.create(
            model="gemini-2.5-flash-preview-tts",
            input=speak_input,
            response_format={"type": "audio"},
            generation_config={"speech_config": [{"voice": "Kore", "language": "ko-KR"}]},
        )
        audio = interaction.output_audio
        if audio is None or not audio.data:
            return jsonify({"error": "오디오 생성 실패"}), 502

        raw = audio.data
        pcm_bytes = base64.b64decode(raw) if isinstance(raw, str) else raw
        sample_rate = audio.sample_rate or 24000
        wav_buf = BytesIO()
        with wave.open(wav_buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_bytes)

        return app.response_class(wav_buf.getvalue(), mimetype="audio/wav")
    except Exception as e:
        return jsonify({"error": f"TTS 요청 실패: {e}"}), 502

@app.route("/<path:path>", methods=["GET", "POST", "OPTIONS"])
def catch_all_routes(path):
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"}), 200

    real_path = (
        request.headers.get("x-forwarded-url") or
        request.headers.get("x-matched-path") or
        request.headers.get("x-vercel-path") or
        request.environ.get("HTTP_X_FORWARDED_URI") or
        request.environ.get("REQUEST_URI") or
        path
    )

    if request.method == "POST":
        payload = request.get_json(force=True, silent=True) or {}
        if "report_type" in payload:
            return api_report()
        elif "action" in payload:
            return api_admin_toggle()
        else:
            return api_chat()

    if "chat" in real_path:
        return api_chat()
    elif "report" in real_path:
        return api_report()
    elif "admin" in real_path:
        return api_admin_toggle()
    elif "hospital" in real_path:
        return api_hospitals()
    elif "tts" in real_path:
        return api_tts()
    elif "stt" in real_path:
        return api_stt()

    return render_template("index.html")

@app.errorhandler(404)
def handle_custom_404(e):
    if request.method == "OPTIONS":
        return jsonify({"status": "ok"}), 200
    if request.method == "POST":
        payload = request.get_json(force=True, silent=True) or {}
        if "report_type" in payload:
            return api_report()
        elif "action" in payload:
            return api_admin_toggle()
        else:
            return api_chat()
    return render_template("index.html")

if __name__ == "__main__":
    # host="0.0.0.0"으로 열어야 같은 와이파이의 다른 기기(휴대폰 등)에서 접속 가능
    app.run(debug=True, port=5000, host="0.0.0.0")