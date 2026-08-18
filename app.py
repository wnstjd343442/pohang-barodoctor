import base64
import glob
import json
import math
import os
import re
import time
import wave
from datetime import datetime, timedelta
from io import BytesIO

import requests
import xmltodict
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

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
DECODING_KEY = os.environ.get("DATA_GO_KR_DECODING_KEY", "")

_gemini_client = None

def get_gemini_client():
    global _gemini_client
    if not GEMINI_API_KEY:
        return None
    if _gemini_client is None:
        try:
            from google import genai
            _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
        except Exception as e:
            print("Gemini client init error:", e)
            _gemini_client = None
    return _gemini_client

DB_FILE = os.path.join(os.path.dirname(__file__), "data", "pohang_hospitals_db.json")
REPORTS_FILE = os.path.join(os.path.dirname(__file__), "data", "reports.json")

# ─────────────────────────────────────────────────────────────
# 🏥 공공데이터포털 (국립중앙의료원) 실시간 응급실 가용병상 캐시 & 조회
# ─────────────────────────────────────────────────────────────
_public_er_cache = {"data": {}, "fetched_at": 0}
PUBLIC_CACHE_TTL = 180  # 3분 캐시

def fetch_realtime_public_er_data():
    """공공데이터포털 getEmrrmRltmUsefulSckbdInfoInqire API 호출"""
    now = time.time()
    if (now - _public_er_cache["fetched_at"]) < PUBLIC_CACHE_TTL and _public_er_cache["data"]:
        return _public_er_cache["data"]

    url = "https://apis.data.go.kr/B552657/ErmctInfoInqireService/getEmrrmRltmUsefulSckbdInfoInqire"
    params = {
        "serviceKey": DECODING_KEY,
        "STAGE1": "경상북도",
        "STAGE2": "포항시",
        "numOfRows": 20
    }
    
    er_mapping = {}
    try:
        res = requests.get(url, params=params, timeout=4, headers={"User-Agent": "Mozilla/5.0"})
        if res.status_code == 200:
            d = xmltodict.parse(res.content)
            items = d.get("response", {}).get("body", {}).get("items", {}).get("item", [])
            if isinstance(items, dict):
                items = [items]
            for item in items:
                name = item.get("dutyName") or ""
                hvec = item.get("hvec")  # 가용 병상수
                hv28 = item.get("hv28")  # 소아 응급 가용수
                
                info = {
                    "live_beds": int(hvec) if (hvec and str(hvec).lstrip('-').isdigit()) else 0,
                    "pediatric_beds": int(hv28) if (hv28 and str(hv28).lstrip('-').isdigit()) else None,
                    "updated_at": datetime.now().strftime("%H:%M")
                }
                
                if "좋은선린" in name:
                    er_mapping["ph-er-01"] = info
                elif "포항의료원" in name:
                    er_mapping["ph-er-02"] = info
                elif "세명기독" in name:
                    er_mapping["ph-er-03"] = info
                elif "성모병원" in name:
                    er_mapping["ph-er-04"] = info
                elif "에스포항" in name:
                    er_mapping["ph-er-05"] = info
                    
            _public_er_cache["data"] = er_mapping
            _public_er_cache["fetched_at"] = now
    except Exception as e:
        print(f"공공데이터포털 실시간 API 에러: {e}")
        
    return _public_er_cache["data"]

def load_hospitals_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            hospitals = json.load(f)
            er_live = fetch_realtime_public_er_data()
            for h in hospitals:
                if h["id"] in er_live:
                    live_info = er_live[h["id"]]
                    h["live_public_data"] = {
                        "is_connected": True,
                        "source": "국립중앙의료원 공공데이터포털",
                        "available_beds": live_info.get("live_beds", 0),
                        "pediatric_beds": live_info.get("pediatric_beds"),
                        "updated_at": live_info.get("updated_at")
                    }
                else:
                    h["live_public_data"] = None
            return hospitals
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
        "title": "관절 / 척추 / 염좌 / 골절",
        "keywords": ["허리", "무릎", "관절", "삐", "접질", "골절", "어깨", "다리", "발목", "손목", "담", "근육통", "통증", "정형외과"],
        "primary_depts": ["정형외과", "통증의학과", "재활의학과"],
        "alt_depts": ["외과", "가정의학과"],
        "advice": "🦴 근골격계/관절 통증으로 판단됩니다. X-ray 검사 및 물리치료, 도수치료가 가능한 정형외과/통증의학과를 추천합니다."
    },
    "neuro": {
        "title": "어지럼증 / 두통 / 신경 질환",
        "keywords": ["어지럼", "저림", "마비", "두통", "편두통", "뇌", "실신", "핑", "어지러", "신경과"],
        "primary_depts": ["신경과", "신경외과", "내과"],
        "alt_depts": ["응급의학과"],
        "advice": "🤕 신경 및 두통 증상으로 판단됩니다. 지속적인 급성 두통이나 심한 어지럼증 시 정밀 진단이 가능한 병원을 추천합니다."
    },
    "trauma": {
        "title": "외상 / 찢어짐 / 출혈 / 화상",
        "keywords": ["상처", "베인", "화상", "찢어짐", "출혈", "피", "봉합", "꿰매", "외과"],
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
    }
}

WEEKDAYS_KR = ["월", "화", "수", "목", "금", "토", "일"]
WEEKDAY_NAMES = {"월요일": 0, "화요일": 1, "수요일": 2, "목요일": 3, "금요일": 4, "토요일": 5, "일요일": 6}

def analyze_symptom_and_intent(text):
    text_lower = text.lower().strip()
    
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
            
    today = datetime.now().date()
    is_sunday = False
    is_night = False
    target_date_str = "오늘"
    
    if "모레" in text:
        d = today + timedelta(days=2)
        target_date_str = f"{d.month}월 {d.day}일({WEEKDAYS_KR[d.weekday()]})"
        is_sunday = (d.weekday() == 6)
    elif "내일" in text:
        d = today + timedelta(days=1)
        target_date_str = f"{d.month}월 {d.day}일({WEEKDAYS_KR[d.weekday()]})"
        is_sunday = (d.weekday() == 6)
    elif "오늘" in text or "지금" in text:
        d = today
        target_date_str = f"오늘 {d.month}월 {d.day}일({WEEKDAYS_KR[d.weekday()]})"
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
                is_sunday = (target_weekday == 6)
                break
        if d is None:
            m = re.search(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", text)
            if m:
                month, day = int(m.group(1)), int(m.group(2))
                try:
                    d = datetime(today.year, month, day).date()
                    target_date_str = f"{month}월 {day}일({WEEKDAYS_KR[d.weekday()]})"
                    is_sunday = (d.weekday() == 6)
                except ValueError:
                    d = today
                    target_date_str = "오늘"
            else:
                target_date_str = "오늘"
                is_sunday = (today.weekday() == 6)
                
    if any(k in text for k in ["야간", "밤", "저녁", "새벽", "늦게", "5시", "6시", "7시", "8시", "9시", "24시"]):
        is_night = True
    if any(k in text for k in ["일요일", "휴일", "주말"]):
        is_sunday = True
        
    target_district = None
    if "양덕" in text:
        target_district = "양덕동"
    elif "장량" in text or "장성" in text or "법원" in text:
        target_district = "장량동"
    elif "두호" in text:
        target_district = "두호동"
    elif "창포" in text:
        target_district = "창포동"
    elif "한동대" in text or "흥해" in text or "초곡" in text:
        target_district = "흥해읍/한동대"
    elif "우현" in text:
        target_district = "우현동"
    elif "이동" in text:
        target_district = "이동"

    # 특정 진료과 직접 검색 (예: 양덕동 내과, 소아과, 이비인후과 등)
    dept_map = {
        "내과": (["내과", "소화기내과", "가정의학과"], ["응급의학과"], "내과 전문 진료 의원을 안내합니다."),
        "이비인후과": (["이비인후과"], ["내과", "가정의학과"], "이비인후과 호흡기/이비인후 질환 전문 의원을 안내합니다."),
        "피부과": (["피부과"], ["가정의학과"], "피부과 질환/진료 의원을 안내합니다."),
        "정형외과": (["정형외과", "통증의학과"], ["외과"], "정형외과/통증의학과 전문 의원을 안내합니다."),
        "소아과": (["소아청소년과", "소아과"], ["이비인후과"], "소아청소년과 전문 의원을 안내합니다."),
        "외과": (["외과", "정형외과"], ["응급의학과"], "외과/상처치료 전문 의원을 안내합니다.")
    }
    for dept_kw, (pri, alt, adv) in dept_map.items():
        if dept_kw in text_lower:
            return {
                "category_key": f"dept_{dept_kw}",
                "is_medical_symptom": True,
                "category_title": f"{dept_kw} 진료 안내",
                "primary_depts": pri,
                "alt_depts": alt,
                "advice": adv,
                "target_date_str": target_date_str,
                "is_sunday": is_sunday,
                "is_night": is_night,
                "target_district": target_district
            }

    # 증상 키워드가 없는 경우
    if matched_cat_key is None:
        if any(w in text_lower for w in ["병원", "의원", "진료", "가까운", "문 연", "응급실", "포사카", "일요일", "야간", "어디", "추천"]):
            return {
                "category_key": "general_hospital",
                "is_medical_symptom": True,
                "category_title": "가까운 진료 가능 병의원",
                "primary_depts": ["내과", "가정의학과", "이비인후과"],
                "alt_depts": ["응급의학과"],
                "advice": "내 위치 기준 진료 가능한 가까운 포항 병의원을 안내합니다.",
                "target_date_str": target_date_str,
                "is_sunday": is_sunday,
                "is_night": is_night,
                "target_district": target_district
            }
        else:
            # 비의료 일상 대화 또는 짧은 텍스트 (예: 바보, 안녕, 하이 등)
            return {
                "category_key": "non_medical",
                "is_medical_symptom": False,
                "category_title": "일반 대화",
                "primary_depts": [],
                "alt_depts": [],
                "advice": "",
                "target_date_str": target_date_str,
                "is_sunday": is_sunday,
                "is_night": is_night,
                "target_district": target_district
            }

    cat_info = SYMPTOM_CATEGORIES[matched_cat_key]
    return {
        "category_key": matched_cat_key,
        "is_medical_symptom": True,
        "category_title": cat_info["title"],
        "primary_depts": cat_info["primary_depts"],
        "alt_depts": cat_info["alt_depts"],
        "advice": cat_info["advice"],
        "target_date_str": target_date_str,
        "is_sunday": is_sunday,
        "is_night": is_night,
        "target_district": target_district
    }

def rank_and_filter_hospitals(hospitals, analysis, user_lat=None, user_lng=None, sort_by="recommend"):
    if analysis.get("category_key") == "non_medical":
        return []
        
    primary_depts = set(analysis["primary_depts"])
    alt_depts = set(analysis["alt_depts"])
    is_sunday = analysis["is_sunday"]
    is_night = analysis["is_night"]
    target_district = analysis["target_district"]
    is_medical = analysis.get("is_medical_symptom", False)
    
    scored_list = []
    
    for h in hospitals:
        if not h.get("is_open_today", True):
            continue
            
        score = 0
        match_reasons = []
        h_depts = set(h.get("departments", []))
        
        common_primary = h_depts.intersection(primary_depts)
        common_alt = h_depts.intersection(alt_depts)
        is_er = bool(h.get("is_emergency"))
        
        # 의학적 증상이 명시된 경우 관련 없는 진료과(예: 배 아픈데 피부과)는 원천 배제!
        if is_medical and not common_primary and not common_alt and not is_er:
            continue
            
        if common_primary:
            score += 50
            match_reasons.append(f"1차 추천: {', '.join(common_primary)}")
        elif common_alt:
            score += 35
            match_reasons.append(f"대안 진료: {', '.join(common_alt)}")
        elif is_er:
            score += 30
            match_reasons.append("24시간 응급진료")
        else:
            score += 5
                
        if is_sunday:
            if h.get("sunday_open") or is_er:
                score += 40
                match_reasons.append("일요일 진료 가능")
            else:
                score -= 35
                
        if is_night:
            if h.get("night_open") or is_er:
                score += 35
                match_reasons.append("야간/24시간 운영")
            else:
                score -= 25
                
        if target_district:
            if target_district in h.get("district", "") or target_district in h.get("address", ""):
                score += 30
                match_reasons.append(f"{target_district} 생활권")
        else:
            if "양덕동" in h.get("district", "") or "장량동" in h.get("district", ""):
                score += 10
                
        dist_km = None
        dist_text = None
        if user_lat is not None and user_lng is not None and h.get("lat") and h.get("lng"):
            dist_km = calculate_distance_km(user_lat, user_lng, h["lat"], h["lng"])
            dist_text = format_distance_and_time(dist_km)
            
            if dist_km is not None:
                if dist_km <= 1.5:
                    score += 25
                    match_reasons.append("반경 1.5km 이내 초근접")
                elif dist_km <= 3.0:
                    score += 15
                elif dist_km <= 6.0:
                    score += 5
                else:
                    score -= min(30, int(dist_km * 2))
                    
        if h.get("posaka") == "O":
            score += 5
            
        if analysis["category_key"] == "fever_cold":
            if h.get("fever_status") == "possible":
                score += 15
            elif h.get("fever_status") == "impossible":
                score -= 50
                
        query = h.get("naver_search_query") or f"{h['name']} 포항"
        naver_map_url = f"https://map.naver.com/p/search/{requests.utils.quote(query)}"
        kakao_map_url = f"https://map.kakao.com/link/to/{requests.utils.quote(h['name'])},{h.get('lat')},{h.get('lng')}"
        
        scored_list.append({
            **h,
            "match_score": score,
            "match_reasons": match_reasons,
            "distance_km": dist_km,
            "distance_text": dist_text,
            "naver_map_url": naver_map_url,
            "kakao_map_url": kakao_map_url
        })
        
    if sort_by == "distance" and user_lat is not None:
        scored_list.sort(key=lambda x: (x.get("distance_km") is None, x.get("distance_km") or 999))
    else:
        scored_list.sort(key=lambda x: x["match_score"], reverse=True)
        
    return scored_list

def generate_gemini_conversational_reply(user_message, analysis, top_hospitals):
    """Gemini 3.6 Flash를 활용해 실제 따뜻하고 전문적인 의료 대화 답변 생성 (할루시네이션 방지 엄격 적용)"""
    client = get_gemini_client()
    if not client:
        return None, "Gemini API 키가 설정되지 않았습니다."
        
    if analysis.get("category_key") == "non_medical":
        prompt = f"""너는 포항 시민과 학생들을 위한 친절한 AI 의료 안내 비서 "포항 바로닥터"야.
사용자가 "{user_message}"라고 입력했어.
의학적 진단 없이, 친절하고 부드럽게 인사하며 "어디가 아프시거나 불편하신 곳이 있으신가요? 증상(예: 장염, 감기, 어지럼증)이나 원하시는 병원을 말씀해 주시면 빠르게 안내해 드릴게요! 😊"라고 1~2문장으로 상냥하게 응답해줘."""
        try:
            res = client.models.generate_content(model="gemini-3.6-flash", contents=prompt)
            return (res.text or "").strip(), None
        except Exception as e:
            return "안녕하세요! 포항 바로닥터입니다. 😊 어디가 아프시거나 불편하신가요? 증상이나 원하시는 진료과를 말씀해 주시면 포항에서 가장 적합한 병원을 찾아드릴게요!", None
        
    hospital_summary = []
    for h in top_hospitals[:4]:
        er_live = f"[실시간 응급병상: {h['live_public_data']['available_beds']}석]" if h.get("live_public_data") else ""
        hospital_summary.append(f"- {h['name']} ({h.get('district', '')}): {h.get('hours_summary', '')} {er_live} 특징: {', '.join(h.get('features', [])[:2])}")
        
    hosp_text = "\n".join(hospital_summary) if hospital_summary else "진료 가능 병원"
    
    prompt = f"""너는 포항 시민과 한동대학교/양덕동 학생들을 위한 친절하고 전문적인 AI 의료 안내 비서 "포항 바로닥터"야.

[사용자 입력]
"{user_message}"

[의학 분석 정보]
- 증상 분류: {analysis['category_title']}
- 추천 진료과: 1차 {', '.join(analysis['primary_depts'])} (대안: {', '.join(analysis['alt_depts'])})
- 희망/기준 날짜: {analysis['target_date_str']} (일요일: {analysis['is_sunday']}, 야간: {analysis['is_night']})

[추천 병원 목록]
{hosp_text}

[엄격한 할루시네이션 방지 지침]
1. 반드시 위에 제공된 [추천 병원 목록]에 존재하는 실제 병원 이름과 정보만 언급할 것. 존재하지 않는 병원이나 지어낸 주소를 절대 말하지 마.
2. 환자의 아픔과 불안에 진심으로 따뜻하게 공감해줘.
3. 환자의 상황(예: 일요일, 야간, 장염 탈수, 고열 등)에 맞춰 수액 치료나 행동 요령을 명확하고 친절하게 설명해줘.
4. 모바일 화면에서 빠르게 읽을 수 있도록 읽기 쉬운 한국어 대화체(3~4문단, 250자 내외)로 작성해줘. 마크다운 볼드(**강조**)를 적절히 사용해줘."""

    try:
        res = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt
        )
        return (res.text or "").strip(), None
    except Exception as e:
        err_str = str(e)
        if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
            err_msg = "⚠️ Gemini API 무료 사용량(분당 20회) 한도에 일시적으로 도달했습니다. (약 20초 후 자동 복구됩니다)"
        else:
            err_msg = f"⚠️ Gemini API 오류: {err_str[:80]}"
        print("Gemini generate error:", e)
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
        primary_str = ", ".join(analysis["primary_depts"]) if analysis.get("primary_depts") else "일반 진료"
        alt_str = ", ".join(analysis.get("alt_depts", []))
        loc_guide = f" (📍 내 위치 기준 {('거리순' if sort_by == 'distance' else '스마트 추천순')} 정렬)" if user_lat else ""
        
        reply_lines = [
            "죄송합니다! 🙇‍♂️ 현재 AI 상담 트래픽이 일시적으로 많아 대화형 답변 생성이 지연되었습니다.",
            "대신 말씀해 주신 증상에 맞춰 **실시간 공공데이터로 검증된 진료 가능 병원 목록을 먼저 바로 안내해 드릴게요!** 😊",
            "",
            f"🔍 **증상 분류**: [{analysis.get('category_title', '의료 안내')}]",
            f"👉 **추천 진료과**: 1순위 `{primary_str}`" + (f" (대안: `{alt_str}`)" if alt_str else ""),
            f"📅 **진료 기준**: {analysis.get('target_date_str', '오늘')}{loc_guide}",
            "",
            analysis.get("advice", "아래 추천 병원 카드를 확인하시고 바로 내비 길찾기나 전화 문의를 이용해 보세요.")
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

@app.route("/api/hospitals", methods=["GET"])
@app.route("/hospitals", methods=["GET"])
def api_hospitals():
    district = request.args.get("district", "").strip()
    tag = request.args.get("tag", "").strip()
    sunday_only = request.args.get("sunday", "").lower() == "true"
    posaka_only = request.args.get("posaka", "").lower() == "true"
    user_lat = request.args.get("lat")
    user_lng = request.args.get("lng")
    sort_by = request.args.get("sort_by", "recommend")
    
    hospitals = load_hospitals_db()
    filtered = []
    
    for h in hospitals:
        if district and district not in h.get("district", "") and district not in h.get("address", ""):
            continue
        if sunday_only and not (h.get("sunday_open") or h.get("is_emergency")):
            continue
        if posaka_only and h.get("posaka") != "O":
            continue
        if tag and tag not in h.get("features", []) and tag not in h.get("departments", []):
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
