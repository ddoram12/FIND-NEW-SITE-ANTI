"""
analyzer.py
────────────────────────────────────────────
위험/지연 사업장 판별 알고리즘 모듈

컬럼 기준 (HsPmsHubService/getHpBasisOulnInfo 실제 응답):
  apprvDay      : 인허가(승인)일 (YYYYMMDD)
  stcnsDay      : 착공일 (빈 문자열이면 미착공)
  useInsptDay   : 사용승인일
  totHhldCnt    : 총 세대수
  totArea       : 연면적(㎡)
  demolExtngGbCd: 기간연장 구분코드
  platPlc       : 지번 주소
  bldNm         : 건물명
"""
import pandas as pd
from datetime import date


def _to_ts(val) -> pd.Timestamp | None:
    """YYYYMMDD 등 다양한 날짜 문자열 → Timestamp. 실패 시 None."""
    if not val:
        return None
    s = str(val).strip()
    if s in ("", " ", "None", "nan", "0"):
        return None
    from datetime import datetime
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y.%m.%d"):
        try:
            return pd.Timestamp(datetime.strptime(s, fmt))
        except Exception:
            continue
    return None


def _months_since(ts: pd.Timestamp) -> float:
    """Timestamp → 오늘까지 경과 개월 수."""
    if ts is None or not isinstance(ts, pd.Timestamp):
        return 0.0
    return (pd.Timestamp(date.today()) - ts).days / 30.44


def run_analysis(
    housing_df: pd.DataFrame,
    subscription_df: pd.DataFrame,
    land_trade_df: pd.DataFrame,
    delay_threshold: int = 6,
    error_margin_pct: float = 5.0,
) -> pd.DataFrame:
    """
    수집된 모든 데이터를 교차 분석하여 위험/지연 사업장 DataFrame을 반환합니다.

    housing_df      : HsPmsHubService/getHpBasisOulnInfo 응답
    subscription_df : 청약홈 분양정보
    land_trade_df   : 토지 실거래가
    delay_threshold : 지연 판단 기준 개월수 (기본 6개월)
    """
    if housing_df.empty:
        return pd.DataFrame()

    df = housing_df.copy()
    today = pd.Timestamp(date.today())

    # ── 규모 필터링 (연면적 2,000㎡ 이하 소형 사업장 제외) ──
    if "totArea" in df.columns:
        df["_totArea_float"] = pd.to_numeric(df["totArea"], errors="coerce").fillna(0.0)
        df = df[df["_totArea_float"] > 2000.0].copy()

    # ── 날짜 컬럼 변환 ──
    df["_app"] = df["apprvDay"].apply(_to_ts)
    df["_stc"] = df["stcnsDay"].apply(_to_ts)
    df["_use"] = df["useInsptDay"].apply(_to_ts)

    # ── 완공 건물 제외 (사용승인일이 등록된 경우 전면 제외) ──
    df = df[df["_use"].isna()].copy()

    # ── 인허가일 없는 것 제외 ──
    df = df[df["_app"].notna()].copy()

    if df.empty:
        return pd.DataFrame()

    # ── 경과 개월 계산 ──
    df["_app_elapsed"] = (today - df["_app"]).dt.days / 30.44
    df["_stc_elapsed"] = df["_stc"].apply(
        lambda d: (today - d).days / 30.44 if pd.notna(d) else None
    )

    result_rows = []

    # ── [유형 A] PF 난항: 착공일 없고 인허가 후 N개월 이상 ──
    mask_a = df["_stc"].isna() & (df["_app_elapsed"] >= delay_threshold)
    for _, row in df[mask_a].iterrows():
        result_rows.append(
            _make_row(row, "A - PF 난항 (미착공)", row["_app_elapsed"], land_trade_df)
        )

    # ── [유형 B] 분양 지연: 착공 후 N개월 이상 청약 공고 없음 ──
    mask_b = df["_stc"].notna() & (df["_stc_elapsed"].fillna(0) >= delay_threshold)
    for _, row in df[mask_b].iterrows():
        if not _check_subscription_match(row, subscription_df):
            result_rows.append(
                _make_row(row, "B - 분양 지연 (미분양공고)", row["_stc_elapsed"], land_trade_df)
            )

    # ── [유형 C] 사업 지연: 기간 연장 신청 (A·B 중복 제외) ──
    mask_c = (
        df["demolExtngGbCd"].astype(str).str.strip().isin(["1", "2", "3", "4", "5"])
        & ~mask_a & ~mask_b
    )
    for _, row in df[mask_c].iterrows():
        result_rows.append(
            _make_row(row, "C - 사업 지연 (기간 연장)", row["_app_elapsed"], land_trade_df)
        )

    if not result_rows:
        return pd.DataFrame()

    result = pd.DataFrame(result_rows)
    result = result.sort_values("지연기간(개월)", ascending=False, na_position="last")
    return result.reset_index(drop=True)


def _make_row(row: pd.Series, risk_type: str, delay_months, land_df: pd.DataFrame) -> dict:
    """결과 딕셔너리 생성 헬퍼."""
    addr = str(row.get("platPlc", "")).strip()

    # 현재 단계
    if pd.notna(row["_stc"]):
        stage = "착공"
    elif pd.notna(row["_app"]):
        stage = "인허가 완료 (미착공)"
    else:
        stage = "사업 승인"

    # 규모
    hhld = str(row.get("totHhldCnt", "")).strip()
    area = str(row.get("totArea", "")).strip()
    try:
        area_fmt = f"{float(area) * 0.3025:,.0f}평" if area and area not in ("0", "") else ""
    except Exception:
        area_fmt = ""
    scale = " / ".join(filter(None, [
        f"{hhld}세대" if hhld and hhld != "0" else "",
        area_fmt,
    ])) or "정보 없음"

    return {
        "위험유형":        risk_type,
        "사업장명":        str(row.get("bldNm", "")).strip() or "정보 없음",
        "주소":            addr,
        "현재단계":        stage,
        "지연기간(개월)":  round(float(delay_months), 1) if delay_months else None,
        "규모(세대/면적)": scale,
        "추정 토지매입가": _estimate_land_cost(addr, land_df),
        "_위도":           None,
        "_경도":           None,
        "_mgmPk":          str(row.get("mgmHsrgstPk", "")),
    }


def _check_subscription_match(row: pd.Series, sub_df: pd.DataFrame) -> bool:
    """청약홈 데이터에 해당 사업지 주소가 매칭되는지 확인."""
    if sub_df.empty:
        return False
    addr = str(row.get("platPlc", "")).replace(" ", "")
    if len(addr) < 8:
        return False
    key = addr[:12]
    return sub_df["HSSPLY_ADRES"].fillna("").str.replace(" ", "").str.contains(key[:8], regex=False).any()


def _estimate_land_cost(plat_plc: str, land_df: pd.DataFrame) -> str:
    """인근 토지 실거래가 합산으로 추정 매입가 계산."""
    if land_df.empty or not plat_plc:
        return "정보 없음"
    parts = plat_plc.strip().split()
    umd = next((p for p in parts if p.endswith(("동", "읍", "면"))), "")
    if not umd:
        return "정보 없음"
    mask = land_df["umdNm"].astype(str).str.contains(umd, na=False)
    nearby = land_df[mask].copy()
    if nearby.empty:
        return "정보 없음"
    def to_int(v):
        try:
            return int(str(v).replace(",", "").strip())
        except Exception:
            return 0
    nearby["_amt"] = nearby["dealAmount"].apply(to_int)
    nearby["_area"] = pd.to_numeric(nearby.get("dealArea", pd.Series(dtype=float)), errors="coerce").fillna(0)
    total_man = nearby.nlargest(5, "_area")["_amt"].sum()
    if total_man <= 0:
        return "정보 없음"
    uk = total_man / 10000
    return f"약 {uk:,.0f}억 원" if uk >= 1 else f"약 {total_man:,}만 원"
