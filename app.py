import base64
import glob
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
from google import genai
from pydub import AudioSegment

# .env 파일에 있는 키들을 환경변수로 읽어들인다 (이 파일은 git에 절대 올라가면 안 됨)
load_dotenv()

app = Flask(__name__)

# ─────────────────────────────────────────────────────────────
# ffmpeg 경로 잡아주기 (pydub이 webm→wav 변환할 때 필요함)
# winget으로 설치하면 이번 터미널 세션엔 PATH가 바로 안 잡힐 수 있어서,
# 설치 폴더를 직접 뒤져서 PATH에 추가해준다.
# ─────────────────────────────────────────────────────────────
def _ensure_ffmpeg_on_path():
    localappdata = os.environ.get("LOCALAPPDATA", "")
    pattern = os.path.join(localappdata, "Microsoft", "WinGet", "Packages", "*FFmpeg*", "**", "ffmpeg.exe")
    for path in glob.glob(pattern, recursive=True):
        bin_dir = os.path.dirname(path)
        if bin_dir not in os.environ["PATH"]:
            os.environ["PATH"] += os.pathsep + bin_dir
        return


_ensure_ffmpeg_on_path()

# 키는 코드에 직접 안 적고 .env 파일(git에 안 올라감)에서 읽어온다.
# 로컬에서 처음 세팅할 땐 .env.example을 복사해서 .env로 만들고 실제 키를 채워넣으면 됨.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
DECODING_KEY = os.environ.get("DATA_GO_KR_DECODING_KEY", "")

_gemini_client = None


def get_gemini_client():
    """Gemini 클라이언트를 필요할 때만 만든다. 키가 안 채워져 있으면 명확한 에러를 낸다."""
    global _gemini_client
    if not GEMINI_API_KEY:
        raise RuntimeError("Gemini API 키가 설정되지 않았어요. .env 파일의 GEMINI_API_KEY를 채워주세요.")
    if _gemini_client is None:
        _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    return _gemini_client

BASE_URL = "https://apis.data.go.kr/B552657/ErmctInfoInqireService/getEgytListInfoInqire"

STAGE1 = "경상북도"
STAGE2_KEYWORD = "포항시"  # 서버 필터를 못 믿으므로, dutyAddr에서 직접 검사한다 (북구+남구 전체)

HEADERS = {"User-Agent": "Mozilla/5.0"}

# 공공데이터포털 API를 매 요청마다 부르지 않도록 잠깐 캐시해둔다.
CACHE_TTL_SECONDS = 300
_cache = {"data": None, "fetched_at": 0}


def fetch_page(page_no, num_of_rows=100):
    """한 페이지 조회. requests가 params를 알아서 정확히 한 번만 인코딩하게 둔다."""
    if not DECODING_KEY:
        raise RuntimeError("공공데이터포털 키가 설정되지 않았어요. .env 파일의 DATA_GO_KR_DECODING_KEY를 채워주세요.")

    params = {
        "serviceKey": DECODING_KEY,
        "STAGE1": STAGE1,
        "STAGE2": STAGE2_KEYWORD,
        "pageNo": page_no,
        "numOfRows": num_of_rows,
    }
    response = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=10)
    response.raise_for_status()

    data_dict = xmltodict.parse(response.content)
    header = data_dict.get("response", {}).get("header", {})
    result_code = header.get("resultCode")
    result_msg = header.get("resultMsg")

    if result_code != "00":
        raise RuntimeError(f"API 에러 응답: {result_code} - {result_msg}")

    body = data_dict.get("response", {}).get("body", {})
    total_count = int(body.get("totalCount", 0))
    items = body.get("items", {})

    if not items:
        return [], total_count

    item_list = items.get("item", [])
    if isinstance(item_list, dict):
        item_list = [item_list]

    return item_list, total_count


def fetch_all_items():
    """totalCount를 보고 필요한 페이지를 전부 순회해서 모은다."""
    all_items = []
    page_no = 1
    num_of_rows = 100

    while True:
        item_list, total_count = fetch_page(page_no, num_of_rows)
        all_items.extend(item_list)

        if page_no * num_of_rows >= total_count:
            break
        page_no += 1

    return all_items


def get_pohang_hospitals():
    """5분 캐시를 두고 포항시(북구+남구) 응급의료기관 목록을 반환한다."""
    now = time.time()
    if _cache["data"] is not None and (now - _cache["fetched_at"]) < CACHE_TTL_SECONDS:
        return _cache["data"]

    all_items = fetch_all_items()
    filtered = [item for item in all_items if STAGE2_KEYWORD in (item.get("dutyAddr") or "")]

    parsed = []
    for item in filtered:
        lat = item.get("wgs84Lat")
        lng = item.get("wgs84Lon")
        address = item.get("dutyAddr") or ""
        if "북구" in address:
            district = "북구"
        elif "남구" in address:
            district = "남구"
        else:
            district = ""
        parsed.append(
            {
                "name": item.get("dutyName"),
                "type": item.get("dutyEmclsName"),
                "district": district,
                "address": address,
                "tel": item.get("dutyTel1"),
                "erTel": item.get("dutyTel3"),
                "lat": float(lat) if lat else None,
                "lng": float(lng) if lng else None,
            }
        )

    _cache["data"] = parsed
    _cache["fetched_at"] = now
    return parsed


# ─────────────────────────────────────────────────────────────
# 프로토타입용 하드코딩 데이터: 진료과 / 운영정보
# 실제 진료과 데이터가 아니라, 데모를 위해 병원명 기준으로 임시로 채운 값.
# (국립중앙의료원 API는 "응급의료기관 등급"만 주고 진료과 정보는 안 줌)
# ─────────────────────────────────────────────────────────────
HOSPITAL_META = {
    "경상북도포항의료원": {"departments": ["내과", "외과", "정형외과"], "hours": "24시간"},
    "의료법인은성의료재단좋은선린병원": {"departments": ["내과", "외과", "정형외과", "신경외과"], "hours": "24시간"},
    "에스포항병원": {"departments": ["정형외과"], "hours": "24시간"},
    "포항성모병원": {"departments": ["내과", "외과", "정형외과", "신경외과", "흉부외과", "소아과"], "hours": "24시간"},
    "포항세명기독병원": {"departments": ["내과", "외과", "산부인과"], "hours": "24시간"},
}

# 증상 키워드 → 진료과 매핑 (키워드 기반 간단 분류, 실제 AI/의학적 판단 아님)
SYMPTOM_RULES = [
    (["배", "복통", "소화", "설사", "구토", "속쓰림", "체함", "장염"], "내과"),
    (["열", "기침", "감기", "몸살", "오한", "콧물"], "내과"),
    (["허리", "무릎", "관절", "삐", "골절", "어깨", "다리", "발목", "손목"], "정형외과"),
    (["어지럼", "저림", "마비", "두통"], "신경외과"),
    (["상처", "베인", "화상", "찢어짐", "출혈"], "외과"),
    (["임신", "생리", "산부인과"], "산부인과"),
    (["아이", "소아", "아기"], "소아과"),
]

WEEKDAYS_KR = ["월", "화", "수", "목", "금", "토", "일"]

# "일요일", "월요일" 처럼 요일 이름으로 말하는 경우 매칭용
WEEKDAY_NAMES = {"월요일": 0, "화요일": 1, "수요일": 2, "목요일": 3, "금요일": 4, "토요일": 5, "일요일": 6}


def classify_symptom(text):
    """키워드 매칭으로 진료과를 대략 추정한다. 못 찾으면 '내과'로 기본 처리."""
    for keywords, department in SYMPTOM_RULES:
        for kw in keywords:
            if kw in text:
                return department
    return "내과"


def parse_date(text):
    """문장에서 '오늘/내일/모레', '일요일' 같은 요일명, 또는 'N월 N일' 패턴을 찾아 날짜를 계산한다. 없으면 오늘."""
    today = datetime.now().date()

    if "모레" in text:
        d = today + timedelta(days=2)
    elif "내일" in text:
        d = today + timedelta(days=1)
    elif "오늘" in text:
        d = today
    else:
        d = None
        for name, target_weekday in WEEKDAY_NAMES.items():
            if name in text:
                # 오늘이 그 요일이면 오늘, 아니면 이번 주 안의 가장 가까운 그 요일로
                diff = (target_weekday - today.weekday()) % 7
                d = today + timedelta(days=diff)
                break

        if d is None:
            m = re.search(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", text)
            if m:
                month, day = int(m.group(1)), int(m.group(2))
                try:
                    d = datetime(today.year, month, day).date()
                except ValueError:
                    d = today
            else:
                d = today

    weekday = WEEKDAYS_KR[d.weekday()]
    return f"{d.month}월 {d.day}일({weekday})"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/chat", methods=["POST"])
def api_chat():
    payload = request.get_json(force=True, silent=True) or {}
    message = (payload.get("message") or "").strip()

    if not message:
        return jsonify({"error": "증상과 날짜를 입력해주세요."}), 400

    department = classify_symptom(message)
    date_str = parse_date(message)

    try:
        hospitals = get_pohang_hospitals()
    except Exception as e:
        return jsonify({"error": str(e)}), 502

    recommended = []
    for h in hospitals:
        meta = HOSPITAL_META.get(h["name"])
        if meta and department in meta["departments"]:
            recommended.append({**h, "departments": meta["departments"], "hours": meta["hours"]})

    # 매칭되는 진료과가 없으면, 어차피 응급실이 있으니 전체를 대안으로 보여준다
    if not recommended:
        for h in hospitals:
            meta = HOSPITAL_META.get(h["name"], {})
            recommended.append(
                {**h, "departments": meta.get("departments", []), "hours": meta.get("hours", "24시간")}
            )

    return jsonify(
        {
            "department": department,
            "date": date_str,
            "reply": f"증상이 {department} 쪽인 것 같네요. {date_str}에 진료 가능한 병원들은 다음과 같습니다:",
            "hospitals": recommended,
        }
    )


@app.route("/api/stt", methods=["POST"])
def api_stt():
    """녹음된 음성(webm 등)을 받아 wav로 변환한 뒤 Gemini로 텍스트 변환한다."""
    audio_file = request.files.get("audio")
    if not audio_file:
        return jsonify({"error": "audio 파일이 없어요."}), 400

    try:
        client = get_gemini_client()
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400

    try:
        # 브라우저 녹음은 보통 webm/opus라 Gemini가 바로 못 읽는다. wav로 변환.
        sound = AudioSegment.from_file(BytesIO(audio_file.read()))
        wav_buf = BytesIO()
        sound.export(wav_buf, format="wav")
        wav_bytes = wav_buf.getvalue()
    except Exception as e:
        return jsonify({"error": f"오디오 변환 실패 (ffmpeg 설치 확인 필요): {e}"}), 500

    try:
        # 오디오는 반드시 base64 "문자열"로 인코딩해서 넘겨야 한다.
        # bytes를 그대로 넘기면 SDK가 텍스트로 착각해서 디코딩하려다 UnicodeDecodeError가 난다.
        audio_b64 = base64.b64encode(wav_bytes).decode("ascii")

        interaction = client.interactions.create(
            model="gemini-3.6-flash",
            input=[
                {"type": "text", "text": "다음 음성을 한국어 텍스트로 정확히 받아써줘. 다른 설명 없이 텍스트만 출력해."},
                {"type": "audio", "data": audio_b64, "mime_type": "audio/wav"},
            ],
        )
        text = (interaction.output_text or "").strip()
        return jsonify({"text": text})
    except Exception as e:
        return jsonify({"error": f"Gemini STT 요청 실패: {e}"}), 502


@app.route("/api/tts", methods=["POST"])
def api_tts():
    """텍스트를 Gemini TTS로 음성(wav)으로 바꿔 그대로 응답 본문에 실어 보낸다."""
    payload = request.get_json(force=True, silent=True) or {}
    text = (payload.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text가 비어있어요."}), 400

    try:
        client = get_gemini_client()
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400

    try:
        # 텍스트만 달랑 주면 모델이 "대답"으로 착각해서 텍스트로 응답해버리는 경우가 있다.
        # "그대로 읽어줘" 지시문을 앞에 붙여서 항상 낭독으로만 처리되게 한다.
        speak_input = f"다음 문장을 한국어로 자연스럽게 그대로 읽어줘. 다른 말 덧붙이지 말고: {text}"

        interaction = client.interactions.create(
            model="gemini-2.5-flash-preview-tts",
            input=speak_input,
            response_format={"type": "audio"},
            generation_config={"speech_config": [{"voice": "Kore", "language": "ko-KR"}]},
        )
        audio = interaction.output_audio
        if audio is None or not audio.data:
            return jsonify({"error": "Gemini가 오디오를 돌려주지 않았어요."}), 502

        raw = audio.data
        pcm_bytes = base64.b64decode(raw) if isinstance(raw, str) else raw

        # Gemini TTS는 헤더 없는 raw PCM(16bit, mono, 24kHz)을 돌려주므로
        # 브라우저에서 재생 가능하도록 WAV 헤더를 직접 씌워준다.
        sample_rate = audio.sample_rate or 24000
        wav_buf = BytesIO()
        with wave.open(wav_buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16bit
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_bytes)

        return app.response_class(wav_buf.getvalue(), mimetype="audio/wav")
    except Exception as e:
        return jsonify({"error": f"Gemini TTS 요청 실패: {e}"}), 502


@app.route("/api/hospitals")
def api_hospitals():
    try:
        hospitals = get_pohang_hospitals()
    except Exception as e:
        return jsonify({"error": str(e)}), 502
    return jsonify(hospitals)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
