"""
region_codes.py
────────────────────────────────────────────
행정구역명 ↔ 코드 변환 유틸리티입니다.
- 법정동 시도/시군구 코드를 API로 가져와 캐싱합니다.
- 시도명 → 토지실거래가 API용 '시/도 약칭' 변환도 포함합니다.
"""
import requests
import streamlit as st

REGCODE_API = "https://grpc-proxy-server-mkvo6j4wsq-du.a.run.app/v1/regcodes"


@st.cache_data(ttl=86400)   # 24시간 캐시
def get_sido_list() -> list[dict]:
    """전국 시/도 목록을 반환합니다. [{code, name}, ...]"""
    try:
        res = requests.get(
            REGCODE_API,
            params={"regcode_pattern": "*00000000"},
            timeout=5
        )
        data = res.json()
        return [
            {"code": item["code"][:2], "name": item["name"]}
            for item in data.get("regcodes", [])
        ]
    except Exception:
        # 서버 오류 시 하드코딩 폴백
        return [
            {"code": "11", "name": "서울특별시"},
            {"code": "26", "name": "부산광역시"},
            {"code": "27", "name": "대구광역시"},
            {"code": "28", "name": "인천광역시"},
            {"code": "29", "name": "광주광역시"},
            {"code": "30", "name": "대전광역시"},
            {"code": "31", "name": "울산광역시"},
            {"code": "41", "name": "경기도"},
            {"code": "43", "name": "충청북도"},
            {"code": "44", "name": "충청남도"},
            {"code": "45", "name": "전라북도"},
            {"code": "46", "name": "전라남도"},
            {"code": "47", "name": "경상북도"},
            {"code": "48", "name": "경상남도"},
            {"code": "50", "name": "제주특별자치도"},
            {"code": "51", "name": "강원특별자치도"},
        ]


@st.cache_data(ttl=86400)
def get_sigungu_list(sido_code: str) -> list[dict]:
    """특정 시도의 시군구 목록을 반환합니다. [{code, name}, ...]"""
    try:
        res = requests.get(
            REGCODE_API,
            params={"regcode_pattern": f"{sido_code}*00000"},
            timeout=5
        )
        data = res.json()
        result = []
        for item in data.get("regcodes", []):
            # 시도 전체 코드(끝이 모두 0)는 제외
            if item["code"].endswith("00000000"):
                continue
            name_parts = item["name"].split()
            if len(name_parts) >= 2:
                result.append({
                    "code": item["code"][:5],
                    "name": " ".join(name_parts[1:])
                })
        return result
    except Exception:
        return []


# 시도명 → 청약홈 검색용 약칭 매핑
SIDO_TO_SUBSCRIPTION_AREA = {
    "서울특별시": "서울",
    "부산광역시": "부산",
    "대구광역시": "대구",
    "인천광역시": "인천",
    "광주광역시": "광주",
    "대전광역시": "대전",
    "울산광역시": "울산",
    "세종특별자치시": "세종",
    "경기도": "경기",
    "충청북도": "충북",
    "충청남도": "충남",
    "전라북도": "전북",
    "전라남도": "전남",
    "경상북도": "경북",
    "경상남도": "경남",
    "제주특별자치도": "제주",
    "강원특별자치도": "강원",
}


def get_subscription_area(sido_name: str) -> str:
    """시도명을 청약홈 검색 지역명으로 변환합니다."""
    return SIDO_TO_SUBSCRIPTION_AREA.get(sido_name, sido_name[:2])
