# 🏥 포항 바로닥터 (Pohang BaroDoctor)
> **공공데이터포털 실시간 응급의료 API & Gemini 3.6 Flash 기반 맞춤형 AI 병원 안내 서비스**

포항 시민과 한동대학교/양덕동 대학생 및 자취생들을 위해 갑작스러운 야간/휴일 질환(장염, 고열, 외상 등) 발생 시 **0-클릭 음성 대화**와 **공공데이터 실시간 응급실 가용병상 연동**, **네이버 지도 원터치 길찾기**를 지원하는 AI 의료 길잡이 서비스입니다.

---

## 🌟 주요 핵심 기능

1. 🤖 **Gemini 3.6 Flash 대화형 AI 트리이지**
   - 사용자가 겪는 증상(예: *"일요일 5시에 장염 걸렸어 어디 가야 해?"*)에 대해 따뜻한 공감과 의학적 행동 요령(수액 치료 권고, 긴급도 판단)을 실시간 생성.
2. 🟢 **공공데이터포털(국립중앙의료원) 실시간 응급병상 연동**
   - `getEmrrmRltmUsefulSckbdInfoInqire` API를 통해 포항시 5대 응급의료기관(좋은선린병원, 포항의료원, 세명기독병원, 포항성모병원, 에스포항병원)의 실시간 잔여 응급병상 및 소아응급 병상 수 표시.
3. 📍 **GPS 기반 최단거리 계산 & 정렬**
   - Haversine 알고리즘을 통한 실시간 거리(m/km) 및 차량/도보 소요 시간 계산.
   - `[⭐ 스마트 추천순]` 및 `[📍 가까운 거리순]` 실시간 정렬 토글.
4. 🗺️ **네이버 지도 원터치 다이렉트 연결**
   - 병원 카드의 `[🗺️ 네이버 지도에서 보기]` 버튼을 누르면 실제 네이버 지도 앱/웹으로 즉시 이동하여 길찾기 및 내비게이션 연결.
5. 💳 **포항 지역 특화 메타데이터**
   - **포항사랑카드** 결제 가능 여부 배지
   - **발열 진료(신속항원검사)** 가능 여부 사전 확인
   - **피부과 전문의/질환 치료** vs **미용 시술** 명확한 구분
6. 🎤 **음성 인식 (Web Speech API + Gemini STT 백업)**
   - 타자 칠 기운이 없는 응급 상황에서도 마이크 버튼 하나로 음성 대화 지원.
7. 📢 **집단지성 사용자 제보 & 심사위원 데모 패널**
   - 실시간 병원 상태 제보 시스템 및 해커톤 시연용 관리자 토글 드로어 완비.

---

## 🛠️ 기술 스택 (Tech Stack)

- **Backend**: Python 3.9+, Flask, Gunicorn, Requests, XMLtoDict
- **AI & LLM**: Google Gemini 3.6 Flash (`google-genai` SDK)
- **Open Data**: 공공데이터포털 국립중앙의료원 응급의료정보서비스 API
- **Frontend**: Vanilla HTML5/CSS3, JavaScript (Pretendard Font, Mobile-First Glassmorphism UI)
- **Map & Integration**: Naver Map Web Integration

---

## 🚀 로컬 실행 방법

```bash
# 1. 가상환경 생성 및 활성화
python3 -m venv .venv
source .venv/bin/activate

# 2. 패키지 설치
pip install -r requirements.txt

# 3. 환경 변수(.env) 설정
GEMINI_API_KEY="your-gemini-api-key"
DATA_GO_KR_DECODING_KEY="your-data-go-kr-decoding-key"

# 4. 서버 실행
python app.py
```

브라우저에서 `http://127.0.0.1:5000` 접속

---

## 📄 라이선스
MIT License
