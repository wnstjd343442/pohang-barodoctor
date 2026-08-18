"""
data/pohang_hospitals_db.json 생성 스크립트 (100% 공공데이터 기반)

출처: 국립중앙의료원 E-Gen 병의원 정보
  - 목록:   POST https://www.e-gen.or.kr/egen/retrieve_hospital_list_grid.do
            (sidoCode=47 경상북도, gugunCode=111 포항 남구 / 113 포항 북구)
  - 진료과: 같은 엔드포인트에 trtPrtCod(진료과 코드)를 넣어 과목별로 재조회 후 역매핑
  - 응급실 전화: 공공데이터포털 ErmctInfoInqireService/getEgytListInfoInqire

이 스크립트가 만들어내는 모든 필드는 위 원본에서 그대로 오거나 원본 값으로 계산된 것이다.
검증 불가능한 항목(포항사랑상품권 가맹 여부, 발열진료 가능 여부)은 값을 만들어내지 않고
"unknown"으로 둔다. 실제로 확인한 정보가 생기면 data/pohang_overrides.json 에 넣으면 병합된다.

사용법:  python3 build_hospitals_db.py
"""

import json
import os
import re
import time
from datetime import date

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_FILE = os.path.join(BASE_DIR, "data", "pohang_hospitals_db.json")
OVERRIDE_FILE = os.path.join(BASE_DIR, "data", "pohang_overrides.json")

EGEN_GRID = "https://www.e-gen.or.kr/egen/retrieve_hospital_list_grid.do"
EGEN_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://www.e-gen.or.kr/egen/search_hospital.do",
    "X-Requested-With": "XMLHttpRequest",
}
GUGUN = {"111": "남구", "113": "북구"}

# E-Gen 진료과목 코드 (search_hospital.do 의 trtPrtCod select 값)
DEPT_CODES = {
    "D022": "가정의학과", "D015": "결핵과", "D040": "구강내과", "D042": "구강병리과",
    "D041": "구강악안면방사선과", "D034": "구강악안면외과", "D054": "구강안면외과",
    "D001": "내과", "D017": "마취통증의학과", "D031": "방사선종양학과", "D032": "병리과",
    "D014": "비뇨의학과", "D050": "사상체질과", "D011": "산부인과", "D025": "산업의학과",
    "D010": "성형외과", "D002": "소아청소년과", "D037": "소아치과", "D003": "신경과",
    "D009": "신경외과", "D007": "심장혈관흉부외과", "D012": "안과", "D018": "영상의학과",
    "D055": "영상치의학과", "D029": "예방의학과", "D043": "예방치과", "D006": "외과",
    "D024": "응급의학과", "D013": "이비인후과", "D053": "작업환경의학과", "D016": "재활의학과",
    "D004": "정신건강의학과", "D008": "정형외과", "D033": "진단검사의학과", "D026": "치과",
    "D036": "치과교정과", "D039": "치과보존과", "D035": "치과보철과", "D019": "치료방사선과",
    "D038": "치주과", "D051": "침구과", "D056": "통합치의학과", "D005": "피부과",
    "D044": "한방내과", "D045": "한방부인과", "D046": "한방소아과", "D048": "한방신경정신과",
    "D047": "한방안이비인후피부과", "D052": "한방응급과", "D049": "한방재활의학과",
    "D021": "해부병리과", "D023": "핵의학과",
}

DAY_KEYS = [
    ("mon", "MONDAY", "월"), ("tue", "TUESDAY", "화"), ("wed", "WEDNESDAY", "수"),
    ("thu", "THURSDAY", "목"), ("fri", "FRIDAY", "금"), ("sat", "SATURDAY", "토"),
    ("sun", "SUNDAY", "일"), ("holiday", "HOLIDAY", "공휴일"),
]

DATA_GO_KR_KEY = os.environ.get("DATA_GO_KR_DECODING_KEY", "")


def egen_post(gugun_code, dept_code="", tries=4):
    payload = {
        "searchType": "general", "jusoType": "", "radioCode": "", "loca": "27",
        "emogdstr": "2724", "sidoCode": "47", "gugunCode": gugun_code,
        "addrhosp": "", "addrroad": "", "roadaddr": "", "emogdesc": "",
        "day": "", "trtPrtCod": dept_code, "time": "", "silson24": "",
    }
    for attempt in range(tries):
        try:
            res = requests.post(EGEN_GRID, data=payload, headers=EGEN_HEADERS, timeout=60)
            return res.json()
        except Exception:
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"E-Gen 조회 실패: gugun={gugun_code} dept={dept_code}")


def fetch_departments():
    """진료과 코드별로 재조회해서 기관코드 -> 진료과 목록 역매핑을 만든다."""
    mapping = {}
    for i, (code, name) in enumerate(DEPT_CODES.items(), 1):
        for gugun in GUGUN:
            for row in egen_post(gugun, code):
                mapping.setdefault(row["EMOGCODE"], set()).add(name)
            time.sleep(1.2)  # E-Gen 연속 호출 시 차단되므로 간격 필요
        print(f"  [{i}/{len(DEPT_CODES)}] {name} … 누적 {len(mapping)}곳")
    return {k: sorted(v) for k, v in mapping.items()}


def fetch_er_tel():
    """응급의료기관 응급실 직통번호 (공공데이터포털)."""
    if not DATA_GO_KR_KEY:
        print("  DATA_GO_KR_DECODING_KEY 미설정 → 응급실 직통번호 생략")
        return {}
    import xml.etree.ElementTree as ET

    url = "https://apis.data.go.kr/B552657/ErmctInfoInqireService/getEgytListInfoInqire"
    out = {}
    page = 1
    while True:
        res = requests.get(
            url,
            params={"serviceKey": DATA_GO_KR_KEY, "STAGE1": "경상북도",
                    "pageNo": page, "numOfRows": 100},
            headers={"User-Agent": "Mozilla/5.0"}, timeout=30,
        )
        root = ET.fromstring(res.content)
        items = root.findall(".//item")
        if not items:
            break
        for item in items:
            if "포항" in (item.findtext("dutyAddr") or ""):
                out[item.findtext("hpid")] = item.findtext("dutyTel3")
        if page * 100 >= int(root.findtext(".//totalCount") or 0):
            break
        page += 1
    return out


def parse_district(addr):
    """도로명주소에서 행정동/읍면 추출. 원본에 없으면 None."""
    paren = re.findall(r"\(([^)]*)\)", addr or "")
    for group in paren:
        for token in group.split(","):
            token = token.strip().strip("()")
            if token.endswith(("동", "읍", "면", "리", "가")):
                return token
    town = re.search(r"포항시\s+[남북]구\s+(\S+[읍면])", addr or "")
    return town.group(1) if town else None


def build_hours(row):
    hours = {}
    for key, field, _ in DAY_KEYS:
        value = row.get(field)
        hours[key] = value if value else None
    return hours


def build_hours_summary(hours):
    weekday = [hours[k] for k in ("mon", "tue", "wed", "thu", "fri") if hours[k]]
    parts = []
    if weekday:
        if len(set(weekday)) == 1:
            parts.append(f"평일 {weekday[0]}")
        else:
            parts.append(" / ".join(
                f"{label} {hours[key]}"
                for key, _, label in DAY_KEYS[:5] if hours[key]
            ))
    parts.append(f"토 {hours['sat']}" if hours["sat"] else "토 휴진")
    parts.append(f"일 {hours['sun']}" if hours["sun"] else "일 휴진")
    if hours["holiday"]:
        parts.append(f"공휴일 {hours['holiday']}")
    return " / ".join(parts) if parts else "진료시간 정보 없음"


def with_er_note(summary, row):
    """응급실 운영 기관은 위 시간이 외래 기준임을 분명히 한다."""
    if row.get("EMOGERYN") == "Y":
        return f"외래 {summary} / 응급실 24시간"
    return summary


def build_features(row, hours, departments):
    feats = []
    if row.get("EMOGERYN") == "Y":
        feats.append("24시간 응급실 운영")
    if row.get("MOONLIGHTYN") == "Y":
        feats.append("달빛어린이병원 (야간·휴일 소아진료)")
    if row.get("NIGHTCAREYN") == "Y":
        feats.append("야간진료")
    if hours["sun"]:
        feats.append(f"일요일 진료 ({hours['sun']})")
    if hours["holiday"]:
        feats.append(f"공휴일 진료 ({hours['holiday']})")
    if hours["sat"]:
        feats.append(f"토요일 진료 ({hours['sat']})")
    if len(departments) > 1:
        feats.append("진료과 " + ", ".join(departments[:4]))
    return feats


def build_description(row, hours, departments, district):
    where = f"{row['GU']} {district}" if district else row["GU"]
    what = ", ".join(departments[:3]) if departments else row.get("CATEGORY2", "")
    bits = [f"{where} 소재 {row.get('CATEGORY2', '')}"]
    if what:
        bits.append(f"진료과목: {what}")
    if row.get("EMOGERYN") == "Y":
        bits.append(f"{row.get('CATEGORY1', '')} (응급실 운영)")
    if hours["sun"]:
        bits.append("일요일 진료 가능")
    elif hours["holiday"]:
        bits.append("공휴일 진료 가능")
    return ". ".join(b for b in bits if b.strip()) + "."


def main():
    print("1) E-Gen 포항 남구/북구 전체 기관 조회")
    rows = []
    for gugun, gu_name in GUGUN.items():
        chunk = egen_post(gugun)
        for row in chunk:
            row["GU"] = gu_name
        rows.extend(chunk)
        print(f"  {gu_name}: {len(chunk)}곳")
        time.sleep(1.2)

    print("2) 진료과목별 역매핑 (약 2분 소요)")
    dept_map = fetch_departments()

    print("3) 응급실 직통번호 조회")
    er_tel = fetch_er_tel()

    fetched_at = date.today().isoformat()
    hospitals = []
    for row in rows:
        code = row["EMOGCODE"]
        district = parse_district(row.get("ADDRROAD"))
        hours = build_hours(row)
        departments = dept_map.get(code, [])
        if not departments and row.get("CATEGORY2"):
            departments = [row["CATEGORY2"]]

        updated = row.get("EMOGUPDT") or ""
        hospitals.append({
            "id": code,
            "name": row["TITLE"],
            "gu": row["GU"],
            "district": district,
            "address": row.get("ADDRROAD"),
            "tel": row.get("TEL"),
            "er_tel": er_tel.get(code),
            "lat": float(row["LAT"]) if row.get("LAT") else None,
            "lng": float(row["LON"]) if row.get("LON") else None,
            "org_type": row.get("CATEGORY2"),
            "departments": departments,
            "hours": hours,
            "hours_summary": with_er_note(build_hours_summary(hours), row),
            "sunday_open": bool(hours["sun"]),
            "holiday_open": bool(hours["holiday"]),
            "night_open": row.get("NIGHTCAREYN") == "Y",
            "is_emergency": row.get("EMOGERYN") == "Y",
            "emergency_level": row.get("CATEGORY1") if row.get("EMOGERYN") == "Y" else None,
            "moonlight_clinic": row.get("MOONLIGHTYN") == "Y",
            "features": build_features(row, hours, departments),
            "description": build_description(row, hours, departments, district),
            # ── 검증된 원본이 없는 항목: 값을 지어내지 않는다 ──
            "posaka": "unknown",
            "posaka_note": "포항사랑상품권 가맹 여부 미확인 (IM샵 앱에서 확인 필요)",
            "fever_status": "unknown",
            "fever_badge": None,
            "fever_note": None,
            "skin_type": None,
            "skin_badge": None,
            "naver_search_query": row["TITLE"],
            "source": {
                "provider": "국립중앙의료원 E-Gen 병의원 정보",
                "code": code,
                "fetched_at": fetched_at,
                "org_updated_at": (
                    f"{updated[0:4]}-{updated[4:6]}-{updated[6:8]}" if len(updated) >= 8 else None
                ),
            },
        })

    # 사람이 직접 확인한 값만 덮어쓴다 (예: 실제로 확인한 포항사랑카드 가맹점)
    if os.path.exists(OVERRIDE_FILE):
        with open(OVERRIDE_FILE, encoding="utf-8") as f:
            overrides = json.load(f)
        by_id = {h["id"]: h for h in hospitals}
        applied = 0
        for key, patch in overrides.items():
            target = by_id.get(key) or next(
                (h for h in hospitals if h["name"] == key), None)
            if target:
                target.update(patch)
                target.setdefault("manual_verified", True)
                applied += 1
        print(f"4) 수동 검증 오버라이드 {applied}건 적용")

    hospitals.sort(key=lambda h: (not h["is_emergency"], h["gu"], h["name"]))
    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(hospitals, f, ensure_ascii=False, indent=2)

    print(f"\n완료: {len(hospitals)}곳 → {OUT_FILE}")
    print(f"  응급의료기관 {sum(1 for h in hospitals if h['is_emergency'])}곳")
    print(f"  일요일 진료 {sum(1 for h in hospitals if h['sunday_open'])}곳")
    print(f"  공휴일 진료 {sum(1 for h in hospitals if h['holiday_open'])}곳")
    print(f"  야간진료 {sum(1 for h in hospitals if h['night_open'])}곳")


if __name__ == "__main__":
    main()
