"""
app.py  ─  PF 부실/지연 사업장 분석 대시보드 (메인)
────────────────────────────────────────────────────
국토교통부 및 한국부동산원 OpenAPI를 활용하여
전국 부동산 개발 지연·부실 사업장을 자동 탐지합니다.

실행: streamlit run app.py
"""
import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
import os
from dotenv import load_dotenv

# ── 내부 모듈 임포트 ──
from region_codes import get_sido_list, get_sigungu_list, get_subscription_area
from api_client import (
    get_bjdong_codes,
    sample_bjdong_codes,
    fetch_housing_permit_parallel,
    fetch_subscription_info,
    fetch_land_trade,
    geocode_address,
)
from analyzer import run_analysis

# ── .env에서 API 키 자동 로드 ──
load_dotenv()
DATA_API_KEY = os.getenv("DATA_API_KEY", "")
KAKAO_API_KEY = os.getenv("KAKAO_API_KEY", "")

# ────────────────────────────────────────────
# 페이지 기본 설정
# ────────────────────────────────────────────
st.set_page_config(
    page_title="PF 부실/지연 사업장 대시보드",
    page_icon="🏢",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── 커스텀 CSS (디자인) ──
st.markdown("""
<style>
    /* 전체 배경: 세련되고 눈이 편안한 밝은 슬레이트톤 */
    .stApp { background-color: #f8fafc; color: #0f172a; }
    
    /* 사이드바: 깔끔한 화이트 배경과 정교한 경계선 */
    section[data-testid="stSidebar"] {
        background-color: #ffffff !important;
        border-right: 1px solid #e2e8f0;
    }
    
    /* 사이드바 안의 텍스트 색상들 가독성 극대화 */
    section[data-testid="stSidebar"] .stMarkdown,
    section[data-testid="stSidebar"] p,
    section[data-testid="stSidebar"] span,
    section[data-testid="stSidebar"] label {
        color: #334155 !important;
        font-weight: 500 !important;
    }
    
    /* 카드 스타일 메트릭: SaaS 스타일의 깔끔한 그림자 화이트 카드 */
    div[data-testid="metric-container"] {
        background-color: #ffffff !important;
        border: 1px solid #e2e8f0 !important;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -2px rgba(0, 0, 0, 0.05) !important;
        border-radius: 12px !important;
        padding: 16px !important;
    }
    
    /* 메트릭 텍스트 대비 향상 */
    div[data-testid="metric-container"] label {
        color: #475569 !important;
        font-weight: 600 !important;
    }
    div[data-testid="metric-container"] div[data-testid="stMetricValue"] {
        color: #0f172a !important;
        font-weight: 700 !important;
    }
    
    /* 헤더 글자색: 깊이감 있고 선명한 차콜 그레이 */
    h1, h2, h3 { color: #0f172a !important; font-weight: 700 !important; }
    
    /* 경고 배지 색상: 밝은 배경에 대비를 맞춰 좀 더 강렬하게 조정 */
    .risk-badge-a { color: #dc2626; font-weight: bold; }
    .risk-badge-b { color: #d97706; font-weight: bold; }
    .risk-badge-c { color: #ca8a04; font-weight: bold; }
    
    /* 분석 시작 버튼: 고급스러운 프리미엄 인디고-블루 그라데이션 */
    .stButton>button[kind="primary"] {
        background: linear-gradient(90deg, #3b82f6, #1d4ed8) !important;
        color: #ffffff !important;
        border: none !important;
        border-radius: 8px !important;
        font-weight: 700 !important;
        letter-spacing: 0.5px !important;
        box-shadow: 0 4px 6px -1px rgba(59, 130, 246, 0.3) !important;
    }
    .stButton>button[kind="primary"]:hover {
        background: linear-gradient(90deg, #1d4ed8, #1e40af) !important;
    }
</style>
""", unsafe_allow_html=True)

# ────────────────────────────────────────────
# 사이드바 (검색 및 설정)
# ────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🔍 검색 및 설정")
    if not DATA_API_KEY:
        st.error("⚠️ 공공데이터 API 키(DATA_API_KEY)가 누락되었습니다. 배포 시 Secrets 설정에 등록해 주세요.")
    if not KAKAO_API_KEY:
        st.warning("⚠️ 카카오맵 API 키(KAKAO_API_KEY)가 누락되어 지도 기능이 작동하지 않습니다.")

    # ── 1. 지역 선택 ──
    st.markdown("### 📍 지역 선택")
    sidos = get_sido_list()
    sido_names = [s["name"] for s in sidos]
    selected_sido_name = st.selectbox("시/도", options=sido_names, key="sido_select")
    selected_sido_code = next(s["code"] for s in sidos if s["name"] == selected_sido_name)

    sigungus = get_sigungu_list(selected_sido_code)
    sigungu_names = ["전체"] + [s["name"] for s in sigungus]
    selected_sigungu_name = st.selectbox("시/군/구", options=sigungu_names, key="sigungu_select")

    # 시군구 코드 결정
    if selected_sigungu_name == "전체":
        selected_sigungu_code = selected_sido_code + "000"  # 시도 전체
    else:
        selected_sigungu_code = next(
            (s["code"] for s in sigungus if s["name"] == selected_sigungu_name),
            selected_sido_code + "000"
        )

    st.markdown("---")

    # ── 2. 분석 기준 설정 ──
    st.markdown("### ⚙️ 분석 기준")
    period_options = {"최근 1년": 12, "최근 3년": 36, "최근 5년": 60, "최근 10년": 120}
    period_label = st.selectbox("분석 기간", options=list(period_options.keys()), index=2)
    period_months = period_options[period_label]

    delay_threshold = st.slider(
        "지연 판단 기준 (개월)",
        min_value=3, max_value=24, value=6, step=1,
        help="인허가/착공일로부터 몇 개월 이상 경과 시 '지연'으로 판단할지 설정합니다."
    )
    error_margin = st.slider(
        "데이터 매칭 허용 오차 (%)",
        min_value=0, max_value=20, value=5, step=1,
        help="대지면적 및 세대수 비교 시 동일 현장으로 간주할 오차 범위입니다."
    )

    st.markdown("---")

    # ── 분석 시작 버튼 ──
    search_btn = st.button(
        "🚀 분석 시작",
        type="primary",
        use_container_width=True,
        disabled=(not DATA_API_KEY)
    )

# ────────────────────────────────────────────
# 메인 화면
# ────────────────────────────────────────────
st.markdown("# 🏢 부동산 개발 지연·부실 사업장 분석 대시보드")
st.markdown(
    "인허가·착공·분양 데이터를 교차 분석하여 **브릿지론 지연** 및 **본PF 미분양 위험** 현장을 자동으로 도출합니다."
)

# ── 분석 결과 저장용 세션 상태 ──
if "result_df" not in st.session_state:
    st.session_state["result_df"] = pd.DataFrame()
if "land_trade_df" not in st.session_state:
    st.session_state["land_trade_df"] = pd.DataFrame()

# ────────────────────────────────────────────
# 분석 실행 로직
# ────────────────────────────────────────────
if search_btn:
    area_display = f"{selected_sido_name}" + (
        f" {selected_sigungu_name}" if selected_sigungu_name != "전체" else " 전체"
    )
    st.info(f"📡 **{area_display}** 데이터를 수집 중입니다. 데이터 양에 따라 1~3분 소요될 수 있습니다...")

    with st.spinner("데이터 수집 중..."):
        progress = st.progress(0, text="① 법정동 코드 목록 수집 중...")

        # [1] 해당 시군구(또는 시도 전체)의 법정동 코드 목록 수집
        bjdong_pairs = get_bjdong_codes(selected_sido_code, selected_sigungu_code)
        if not bjdong_pairs:
            st.warning("📭 해당 지역의 법정동 정보를 가져오지 못했습니다. 지역 설정을 확인해 주세요.")
            st.stop()

        # 시군구별 균등 분배 샘플링 (최대 60개 동 선정)
        sampled_pairs = sample_bjdong_codes(bjdong_pairs, limit=60)

        progress.progress(10, text=f"② 건축HUB 데이터 병렬 초고속 수집 중... (샘플링 {len(sampled_pairs)}개 법정동)")

        # [2] 병렬 초고속 수집 기동
        housing_df = fetch_housing_permit_parallel(sampled_pairs, DATA_API_KEY)

        progress.progress(65, text="③ 청약홈 분양정보 수집 중...")

        # [3] 청약홈 분양정보
        sub_area = get_subscription_area(selected_sido_name)
        subscription_df = fetch_subscription_info(sub_area, DATA_API_KEY)
        progress.progress(75, text="④ 토지 매매 실거래가 수집 중...")

        # [4] 토지 실거래가
        land_trade_df = fetch_land_trade(selected_sigungu_code, DATA_API_KEY, months=period_months)
        st.session_state["land_trade_df"] = land_trade_df
        progress.progress(85, text="⑤ 위험 사업장 분석 중...")

        # [5] 분석 실행
        result_df = run_analysis(
            housing_df, subscription_df, land_trade_df,
            delay_threshold=delay_threshold,
            error_margin_pct=float(error_margin)
        )

        # [6] 좌표 변환 (카카오맵) - 최대 50건만 (속도 절충)
        if not result_df.empty and KAKAO_API_KEY:
            lats, lngs = [], []
            for addr in result_df["주소"].head(50):
                lat, lng = geocode_address(addr, KAKAO_API_KEY)
                lats.append(lat)
                lngs.append(lng)
            # 50건 이후는 None 패딩
            while len(lats) < len(result_df):
                lats.append(None)
                lngs.append(None)
            result_df["_위도"] = lats
            result_df["_경도"] = lngs

        st.session_state["result_df"] = result_df
        progress.progress(100, text="✅ 분석 완료!")



    if result_df.empty:
        st.warning(
            "📭 해당 조건의 지연/위험 사업장이 발견되지 않았습니다.\n\n"
            "- 선택한 지역이나 분석 기간을 다르게 설정해 보세요.\n"
            "- 기준 기간 내에 조건에 맞는 지연/미착공 사업장이 없을 수 있습니다."
        )
    else:
        st.success(f"✅ **{len(result_df)}개**의 위험/지연 사업장이 발견되었습니다!")

# ────────────────────────────────────────────
# KPI 요약 지표
# ────────────────────────────────────────────
result_df = st.session_state.get("result_df", pd.DataFrame())
st.markdown("---")
st.markdown("### 📊 요약 지표")

col1, col2, col3, col4 = st.columns(4)

total = len(result_df)
count_a = len(result_df[result_df["위험유형"].str.contains("A", na=False)]) if not result_df.empty else 0
count_b = len(result_df[result_df["위험유형"].str.contains("B", na=False)]) if not result_df.empty else 0
count_c = len(result_df[result_df["위험유형"].str.contains("C", na=False)]) if not result_df.empty else 0

with col1:
    st.metric("🔴 PF 난항 (미착공)", f"{count_a} 건")
with col2:
    st.metric("🟠 분양 지연 (미공고)", f"{count_b} 건")
with col3:
    st.metric("🟡 사업 지연 (기간 연장)", f"{count_c} 건")
with col4:
    st.metric("📋 총 위험 사업장", f"{total} 건")

st.markdown("---")

# ────────────────────────────────────────────
# 탭: 지도 / 테이블
# ────────────────────────────────────────────
tab1, tab2 = st.tabs(["🗺️ 위험 사업장 지도", "📋 상세 결과 테이블"])

# ── 지도 탭 ──
with tab1:
    st.markdown("### 위험 사업장 위치 지도")
    st.markdown(
        "🔴 **빨간 핀**: 1년 이상 지연 &nbsp;|&nbsp; "
        "🟠 **주황 핀**: 6개월~1년 &nbsp;|&nbsp; "
        "🟡 **노란 핀**: 6개월 이하"
    )

    # 지도 중심 좌표 결정
    if not result_df.empty:
        valid_coords = result_df.dropna(subset=["_위도", "_경도"])
        if not valid_coords.empty:
            center_lat = valid_coords["_위도"].mean()
            center_lng = valid_coords["_경도"].mean()
        else:
            center_lat, center_lng = 36.5, 127.5  # 한국 중심
    else:
        center_lat, center_lng = 36.5, 127.5

    m = folium.Map(location=[center_lat, center_lng], zoom_start=11, tiles="CartoDB positron")

    # 마커 추가
    if not result_df.empty:
        for _, row in result_df.iterrows():
            lat = row.get("_위도")
            lng = row.get("_경도")
            if pd.isna(lat) or pd.isna(lng):
                continue

            delay = row.get("지연기간(개월)", 0) or 0
            try:
                delay = float(delay)
            except:
                delay = 0

            # 지연 기간에 따라 마커 색상 결정
            if delay >= 12:
                color = "red"
            elif delay >= 6:
                color = "orange"
            else:
                color = "beige"

            popup_html = f"""
            <div style='font-family: sans-serif; min-width: 200px;'>
                <b>{row.get('사업장명', '정보 없음')}</b><br>
                <hr style='margin:4px 0;'>
                유형: {row.get('위험유형', '-')}<br>
                주소: {row.get('주소', '-')}<br>
                현재 단계: {row.get('현재단계', '-')}<br>
                지연 기간: {delay}개월<br>
                규모: {row.get('규모(세대/면적)', '-')}<br>
                추정 매입가: {row.get('추정 토지매입가', '-')}
            </div>
            """
            folium.Marker(
                location=[lat, lng],
                popup=folium.Popup(popup_html, max_width=280),
                tooltip=row.get("사업장명", "사업장"),
                icon=folium.Icon(color=color, icon="home", prefix="fa"),
            ).add_to(m)
    else:
        # 데이터 없을 때 기본 안내 마커
        folium.Marker(
            [36.5, 127.5],
            popup="지역을 선택하고 '분석 시작'을 눌러주세요.",
            icon=folium.Icon(color="gray", icon="info-sign"),
        ).add_to(m)

    st_folium(m, width="100%", height=550, returned_objects=[])

# ── 테이블 탭 ──
with tab2:
    st.markdown("### 위험/지연 사업장 상세 목록")

    if not result_df.empty:
        # 표시용 컬럼만 선택
        display_cols = ["위험유형", "사업장명", "주소", "현재단계", "지연기간(개월)", "규모(세대/면적)", "추정 토지매입가"]
        display_df = result_df[display_cols].copy()

        # 유형별 색상 필터
        risk_filter = st.multiselect(
            "위험 유형 필터",
            options=display_df["위험유형"].unique().tolist(),
            default=display_df["위험유형"].unique().tolist()
        )
        filtered = display_df[display_df["위험유형"].isin(risk_filter)]

        # 지연 기간 정렬
        sort_col = st.selectbox("정렬 기준", options=["지연기간(개월)", "위험유형", "현재단계"], index=0)
        filtered = filtered.sort_values(by=sort_col, ascending=False)

        st.dataframe(
            filtered,
            use_container_width=True,
            height=450,
            column_config={
                "위험유형": st.column_config.TextColumn("⚠️ 위험 유형", width="medium"),
                "지연기간(개월)": st.column_config.NumberColumn("지연기간 (개월)", format="%.1f"),
                "추정 토지매입가": st.column_config.TextColumn("💰 추정 토지매입가"),
            }
        )

        # CSV 다운로드
        csv = filtered.to_csv(index=False, encoding="utf-8-sig")
        st.download_button(
            label="📥 결과 다운로드 (CSV)",
            data=csv,
            file_name="pf_risk_analysis.csv",
            mime="text/csv",
            use_container_width=True,
        )


    else:
        st.info("사이드바에서 지역을 선택하고 '🚀 분석 시작' 버튼을 눌러주세요.")

# ── 푸터 ──
st.markdown("---")
st.markdown(
    "<div style='text-align:center; color:#555; font-size:12px;'>"
    "데이터 출처: 국토교통부 건축HUB · 한국부동산원 청약홈 · 국토부 실거래가 (공공데이터포털) "
    "| 본 대시보드는 참고용이며 투자 판단의 책임은 사용자에게 있습니다."
    "</div>",
    unsafe_allow_html=True
)
