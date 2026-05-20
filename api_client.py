"""
api_client.py
────────────────────────────────────────────
각 정부 API 호출을 담당하는 모듈입니다.

[검증 완료된 API]
- 국토부 건축HUB 주택인허가: HsPmsHubService/getHpBasisOulnInfo (XML)
- 한국부동산원 청약홈: api.odcloud.kr (JSON)
- 국토부 토지 매매 실거래가: RTMSDataSvcLandTrade (XML)
- 카카오맵: 주소→좌표 변환 (JSON)

* 핵심 수정: serviceKey를 URL에 직접 삽입 (이중인코딩 방지)
* 페이징(paging): numOfRows 단위로 나눠 전체 데이터 수집
* 타임아웃: 각 요청 15초 제한
* 에러 처리: 오류 시 빈 DataFrame 반환 + 안내 메시지
"""
import requests
import xml.etree.ElementTree as ET
import pandas as pd
import time
import streamlit as st
from urllib.parse import urlencode

# ── 공통 설정 ──
PAGE_SIZE = 100          # 한 번에 불러올 데이터 수
REQUEST_DELAY = 0.2      # API 과부하 방지 딜레이(초)
TIMEOUT = 15             # 요청 타임아웃(초)
MAX_PAGES = 50           # 최대 페이지 수 (50 * 100 = 5,000건)

# ── 검증된 API Base URL ──
HS_PMS_URL = "http://apis.data.go.kr/1613000/HsPmsHubService/getHpBasisOulnInfo"
SUBSCRIPTION_URL = "https://api.odcloud.kr/api/ApplyhomeInfoDetailSvc/v1/getAPTLttotPblancDetail"
LAND_TRADE_URL = "http://apis.data.go.kr/1613000/RTMSDataSvcLandTrade/getRTMSDataSvcLandTrade"
KAKAO_GEO_URL = "https://dapi.kakao.com/v2/local/search/address.json"


def _build_url(base_url: str, api_key: str, params: dict) -> str:
    """
    serviceKey를 URL에 직접 삽입하여 이중인코딩을 방지합니다.
    한국 공공데이터 API는 이 방식이 안전합니다.
    """
    other = urlencode(params)
    return f"{base_url}?serviceKey={api_key}&{other}"


def _parse_xml_items(xml_text: str) -> tuple[int, list[dict]]:
    """
    XML 응답을 파싱하여 (전체건수, 아이템 리스트)를 반환합니다.
    """
    try:
        root = ET.fromstring(xml_text)
        result_code = root.findtext(".//resultCode") or ""
        # 정상 코드: "00" 또는 "000"
        if result_code not in ("00", "000", "0000"):
            return 0, []
        total = int(root.findtext(".//totalCount") or 0)
        items = root.findall(".//item")
        rows = [{child.tag: (child.text or "").strip() for child in item} for item in items]
        return total, rows
    except ET.ParseError:
        return 0, []


# ────────────────────────────────────────────
# [1] 국토부 건축HUB 주택인허가 정보
#     URL: HsPmsHubService/getHpBasisOulnInfo
#     응답: XML
#     주요 컬럼:
#       - platPlc: 지번 주소
#       - apprvDay: 인허가(승인)일 (YYYYMMDD)
#       - stcnsDay: 착공일 (비어있으면 미착공)
#       - useInsptDay: 사용승인일
#       - totHhldCnt: 총 세대수
#       - totArea: 연면적(㎡)
#       - demolExtngGbCd: 멸실·연장 구분코드 (기간연장 여부)
# ────────────────────────────────────────────
def fetch_housing_permit(sigungu_cd: str, bjdong_cd: str, api_key: str) -> pd.DataFrame:
    """
    국토교통부 건축HUB 주택인허가정보를 가져옵니다.
    sigungu_cd: 5자리 시군구코드 (예: '11680' = 서울 강남구)
    bjdong_cd:  5자리 법정동코드 (예: '10300' = 개포동) - "" 이면 전체
    반환: 인허가 정보 DataFrame
    """
    all_items = []
    page = 1

    while page <= MAX_PAGES:
        params = {
            "sigunguCd": sigungu_cd,
            "numOfRows": str(PAGE_SIZE),
            "pageNo": str(page),
        }
        if bjdong_cd:
            params["bjdongCd"] = bjdong_cd

        url = _build_url(HS_PMS_URL, api_key, params)
        try:
            r = requests.get(url, timeout=TIMEOUT)
            if r.status_code != 200:
                st.warning(f"건축HUB 주택인허가 API 오류 (HTTP {r.status_code})")
                break

            total_count, rows = _parse_xml_items(r.text)
            if not rows:
                break

            all_items.extend(rows)

            if len(all_items) >= total_count:
                break
            page += 1
            time.sleep(REQUEST_DELAY)

        except requests.exceptions.Timeout:
            st.warning("건축HUB 주택인허가 API 타임아웃. 수집된 데이터만 사용합니다.")
            break
        except Exception as e:
            st.warning(f"건축HUB 주택인허가 API 오류: {e}")
            break

    if not all_items:
        return pd.DataFrame()

    return pd.DataFrame(all_items)


# ────────────────────────────────────────────
# [2] 법정동 코드 목록 가져오기 (시군구 → 법정동 목록)
#     법정동별로 인허가 API를 호출할 때 사용
# ────────────────────────────────────────────
def get_bjdong_codes(sido_code: str, sigungu_code: str) -> list[tuple[str, str]]:
    """
    법정동 코드 API에서 특정 시군구(또는 시도 전체)의 (sigunguCd, bjdongCd) 목록을 가져옵니다.
    sigungu_code가 '26000'처럼 전체인 경우 해당 시도(26) 전체의 법정동 목록을 가져옵니다.
    """
    try:
        url = f"https://grpc-proxy-server-mkvo6j4wsq-du.a.run.app/v1/regcodes"
        
        def fetch_for_pattern(pattern):
            r = requests.get(url, params={"regcode_pattern": pattern}, timeout=5)
            data = r.json()
            pairs = []
            for item in data.get("regcodes", []):
                code = item["code"]
                if len(code) == 10 and not code.endswith("00000"):
                    sigungu = code[:5]
                    bjdong = code[5:10]
                    if (sigungu, bjdong) not in pairs:
                        pairs.append((sigungu, bjdong))
            return pairs

        if str(sigungu_code).endswith("000"):
            return fetch_for_pattern(f"{sido_code}*")
            
        res = fetch_for_pattern(f"{sigungu_code}*")
        if not res and len(str(sigungu_code)) == 5 and str(sigungu_code).endswith("0"):
            res = fetch_for_pattern(f"{sigungu_code[:4]}*")
        return res
    except Exception:
        return []


def sample_bjdong_codes(pairs: list[tuple[str, str]], limit: int = 60) -> list[tuple[str, str]]:
    """
    시군구별로 균등 배분 수집 알고리즘(Round-Robin Sigungu Sampling)을 적용하여
    지리적으로 고르게 법정동 쌍을 샘플링합니다. (최대 limit개)
    """
    if not pairs:
        return []
    
    # sigunguCd별로 그룹화
    groups = {}
    for sigungu, bjdong in pairs:
        if sigungu not in groups:
            groups[sigungu] = []
        groups[sigungu].append((sigungu, bjdong))
    
    sigungus = list(groups.keys())
    if not sigungus:
        return []
    
    sampled = []
    idx = 0
    while len(sampled) < limit:
        added = False
        for sigungu in sigungus:
            if idx < len(groups[sigungu]):
                sampled.append(groups[sigungu][idx])
                added = True
            if len(sampled) >= limit:
                break
        if not added:
            break
        idx += 1
        
    return sampled


def fetch_housing_permit_parallel(pairs: list[tuple[str, str]], api_key: str, max_workers: int = 15) -> pd.DataFrame:
    """
    여러 (sigunguCd, bjdongCd) 쌍에 대해 건축HUB 주택인허가 데이터를 병렬로 동시 수집하여 결합합니다.
    """
    if not pairs:
        return pd.DataFrame()
    
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    dfs = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(fetch_housing_permit, sigungu, bjdong, api_key): (sigungu, bjdong)
            for sigungu, bjdong in pairs
        }
        for future in futures:
            try:
                df_part = future.result()
                if df_part is not None and not df_part.empty:
                    dfs.append(df_part)
            except Exception:
                pass
                
    if not dfs:
        return pd.DataFrame()
    
    combined = pd.concat(dfs, ignore_index=True)
    if "mgmHsrgstPk" in combined.columns:
        combined = combined.drop_duplicates(subset="mgmHsrgstPk")
    return combined



# ────────────────────────────────────────────
# [3] 한국부동산원 청약홈 분양공고 정보 (odcloud)
# ────────────────────────────────────────────
def fetch_subscription_info(area_name: str, api_key: str) -> pd.DataFrame:
    """
    한국부동산원 청약홈 분양정보를 가져옵니다.
    area_name: 지역명 (예: '서울', '경기', '부산')
    반환: 분양 공고 DataFrame
    """
    all_items = []
    page = 1

    while page <= MAX_PAGES:
        params = {
            "serviceKey": api_key,
            "page": page,
            "perPage": PAGE_SIZE,
        }
        try:
            r = requests.get(SUBSCRIPTION_URL, params=params, timeout=TIMEOUT)
            if r.status_code != 200:
                st.warning(f"청약홈 API 오류 (HTTP {r.status_code})")
                break

            data = r.json()
            total_count = data.get("totalCount", 0)
            items = data.get("data", [])

            if not items:
                break

            # 지역 필터링
            if area_name and area_name != "전체":
                items = [
                    item for item in items
                    if area_name in str(item.get("SUBSCRPT_AREA_CODE_NM", ""))
                    or area_name in str(item.get("HSSPLY_ADRES", ""))
                ]

            all_items.extend(items)

            if (page * PAGE_SIZE) >= total_count:
                break
            page += 1
            time.sleep(REQUEST_DELAY)

        except requests.exceptions.Timeout:
            st.warning("청약홈 API 타임아웃. 수집된 데이터만 사용합니다.")
            break
        except Exception as e:
            st.warning(f"청약홈 API 오류: {e}")
            break

    if not all_items:
        return pd.DataFrame()

    return pd.DataFrame(all_items)


# ────────────────────────────────────────────
# [4] 국토부 토지 매매 실거래가 (XML 파싱)
# ────────────────────────────────────────────
def fetch_land_trade(lawd_cd: str, api_key: str, months: int = 60) -> pd.DataFrame:
    """
    국토교통부 토지 매매 실거래가 데이터를 가져옵니다.
    lawd_cd: 5자리 시군구코드 (예: '11680')
    months: 최근 몇 개월치 (기본 60 = 5년)
    반환: 토지 거래 DataFrame
    """
    import datetime
    all_items = []

    today = datetime.date.today()
    ym_list = [
        f"{(today.replace(day=1) - datetime.timedelta(days=30 * i)).strftime('%Y%m')}"
        for i in range(months)
    ]

    for ym in ym_list:
        page = 1
        while page <= 10:
            params = {
                "LAWD_CD": lawd_cd,
                "DEAL_YMD": ym,
                "numOfRows": str(PAGE_SIZE),
                "pageNo": str(page),
            }
            url = _build_url(LAND_TRADE_URL, api_key, params)
            try:
                r = requests.get(url, timeout=TIMEOUT)
                if r.status_code != 200:
                    break

                total_count, rows = _parse_xml_items(r.text)
                if not rows:
                    break

                for row in rows:
                    row["dealYM"] = ym
                all_items.extend(rows)

                if (page * PAGE_SIZE) >= total_count:
                    break
                page += 1
                time.sleep(REQUEST_DELAY)

            except requests.exceptions.Timeout:
                break
            except Exception:
                break

    if not all_items:
        return pd.DataFrame()

    return pd.DataFrame(all_items)


# ────────────────────────────────────────────
# [5] 카카오맵 주소 → 위도/경도 변환
# ────────────────────────────────────────────
def geocode_address(address: str, kakao_key: str) -> tuple:
    """
    카카오맵 API를 사용해 주소를 위도/경도로 변환합니다.
    반환: (위도, 경도) 또는 (None, None)
    """
    if not address or not kakao_key:
        return None, None

    headers = {"Authorization": f"KakaoAK {kakao_key}"}
    params = {"query": address, "analyze_type": "similar"}

    try:
        r = requests.get(KAKAO_GEO_URL, headers=headers, params=params, timeout=TIMEOUT)
        data = r.json()
        docs = data.get("documents", [])
        if docs:
            return float(docs[0]["y"]), float(docs[0]["x"])
    except Exception:
        pass

    return None, None
