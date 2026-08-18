import json

import requests
import xmltodict

# 1. 공공데이터포털 마이페이지 > 활용신청 현황에서 확인한 "Decoding" 키를 원문 그대로 넣기
#    (+, /, = 같은 특수문자가 살아있는 상태. 절대 미리 encode 하지 말 것)
DECODING_KEY = "64LDgBQk8Bli+mNF0V3XkVCkoIt08a8VOGr+247v1lHjcTdPrJdugvtapCV7rXqzD8L/1gC0/mSP3/S5BelSBQ=="

BASE_URL = "https://apis.data.go.kr/B552657/ErmctInfoInqireService/getEgytListInfoInqire"

STAGE1 = "경상북도"
STAGE2_KEYWORD = "포항시 북구"  # 서버 필터를 못 믿으므로, 이 문자열을 dutyAddr에서 직접 검사한다

HEADERS = {"User-Agent": "Mozilla/5.0"}


def fetch_page(page_no, num_of_rows=100):
    """한 페이지 조회. requests가 params를 알아서 정확히 한 번만 인코딩하게 둔다."""
    params = {
        "serviceKey": DECODING_KEY,
        "STAGE1": STAGE1,
        "STAGE2": STAGE2_KEYWORD,  # 서버가 필터링을 제대로 안 해도, 최대한 좁혀서 요청은 해본다
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


def fetch_hospital_data():
    try:
        all_items = fetch_all_items()

        # 서버 STAGE2 필터를 신뢰하지 않고, 주소 문자열로 다시 한번 직접 필터링
        filtered = [item for item in all_items if STAGE2_KEYWORD in (item.get("dutyAddr") or "")]

        if not filtered:
            print("조회된 데이터가 없습니다.")
            return

        print(f"=== [포항시 북구 응급의료기관 조회 결과 : 총 {len(filtered)}건] ===\n")

        parsed_results = []
        for idx, item in enumerate(filtered, 1):
            hospital = {
                "번호": idx,
                "기관명": item.get("dutyName"),
                "구분": item.get("dutyEmclsName"),
                "주소": item.get("dutyAddr"),
                "대표전화": item.get("dutyTel1"),
                "응급실전화": item.get("dutyTel3"),
                "위도": item.get("wgs84Lat"),
                "경도": item.get("wgs84Lon"),
            }
            parsed_results.append(hospital)

            print(f"[{idx}] {hospital['기관명']} ({hospital['구분']})")
            print(f"    - 주소: {hospital['주소']}")
            print(f"    - 전화번호: {hospital['대표전화']} / 응급실: {hospital['응급실전화']}")
            print(f"    - 좌표: ({hospital['위도']}, {hospital['경도']})\n")

        with open("pohang_hospitals.json", "w", encoding="utf-8") as f:
            json.dump(parsed_results, f, ensure_ascii=False, indent=2)
        print(">> 'pohang_hospitals.json' 파일 저장 완료!")

    except Exception as e:
        print(f"에러 발생: {e}")


if __name__ == "__main__":
    fetch_hospital_data()