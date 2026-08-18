# 병원찾기서비스

포항시 응급의료기관을 증상/날짜 기반으로 추천해주는 챗봇 프로토타입. 음성 입력(STT)/출력(TTS) 지원.

## 팀원 설치 가이드

### 1. 클론

```bash
git clone https://github.com/Shonny-cloud/-.git
cd -
```

### 2. 패키지 설치

```bash
pip install -r requirements.txt
```

### 3. ffmpeg 설치 (음성 기능에 필요)

Windows라면:
```bash
winget install --id=Gyan.FFmpeg -e
```
설치 후 터미널을 새로 열어야 PATH에 적용돼요.

### 4. `.env` 파일 만들기

`.env.example`을 복사해서 `.env`로 이름을 바꾸세요:
```bash
cp .env.example .env
```

그리고 `.env` 파일을 열어서, **팀 카톡/디스코드로 전달받은 실제 키 값**을 채워넣으세요:
```
GEMINI_API_KEY=실제_키_값
DATA_GO_KR_DECODING_KEY=실제_키_값
```

⚠️ `.env`는 git에 올라가지 않는 파일이에요(`.gitignore`에 등록됨). 절대 커밋하지 마세요.

### 5. 실행

```bash
python app.py
```

`http://127.0.0.1:5000` 접속하면 됩니다.

## 프로젝트 구조

```
app.py               # Flask 백엔드 (병원 검색 + 진료과 분류 + STT/TTS)
templates/index.html # 챗봇 화면
.env                 # 실제 키 (로컬 전용, git 제외)
.env.example          # 키 템플릿 (git 포함)
requirements.txt      # 패키지 목록
```

## 사용 기술

- **병원 데이터**: 공공데이터포털 (국립중앙의료원 응급의료기관 정보 조회 서비스)
- **음성 입력/출력**: Gemini API (STT/TTS)
- **백엔드**: Flask
