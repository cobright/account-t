import streamlit as st
import pandas as pd
import json
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore
from pathlib import Path

# =========================================================
# 1. 시스템 설정 및 Firebase 초기화
# =========================================================
st.set_page_config(page_title="Accoun-T Cloud", layout="wide", page_icon="☁️")

# [수정 후] Secrets에서 읽기 (붙여넣으세요)
# .toml 파일에 적은 [firestore] 섹션을 딕셔너리로 가져옴
key_dict = dict(st.secrets["firestore"])

# Streamlit의 toml 파서가 \n을 문자로 인식할 수 있어서 줄바꿈 문자 처리
if "private_key" in key_dict:
    key_dict["private_key"] = key_dict["private_key"].replace("\\n", "\n")

cred = credentials.Certificate(key_dict)

# 이후 코드는 동일
if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)
st.session_state.firestore_db = firestore.client()

# 세션 캐싱을 이용해 한 번만 연결
if "firestore_db" not in st.session_state:
    # 앱이 리로드될 때마다 초기화되지 않도록 처리
    if not firebase_admin._apps:
        try:
            cred = credentials.Certificate(KEY_PATH)
            firebase_admin.initialize_app(cred)
        except Exception as e:
            st.error(f"🔥 Firebase 연결 실패: {e}")
            st.stop()
    st.session_state.firestore_db = firestore.client()

db = st.session_state.firestore_db

# =========================================================
# 2. 데이터 핸들링 함수 (CRUD with Firestore)
# =========================================================
@st.cache_data(ttl=60) # 60초마다 캐시 갱신 (데이터 절약)
def get_all_questions():
    """모든 문제 가져오기"""
    docs = db.collection("questions").stream()
    # Firestore 문서를 딕셔너리로 변환
    return [doc.to_dict() for doc in docs]

def save_question(question_data):
    """문제 저장 또는 수정 (Upsert)"""
    try:
        q_id = question_data['question_id']
        db.collection("questions").document(q_id).set(question_data)
        get_all_questions.clear() # 캐시 초기화 (즉시 반영)
        return True
    except Exception as e:
        st.error(f"저장 실패: {e}")
        return False

def delete_question(q_id):
    """문제 삭제"""
    try:
        db.collection("questions").document(q_id).delete()
        get_all_questions.clear() # 캐시 초기화
        return True
    except Exception as e:
        st.error(f"삭제 실패: {e}")
        return False

# =========================================================
# 3. PV 엔진 로직 (기존과 동일)
# =========================================================
def calculate_bond_schedule(face, c_rate, m_rate, periods):
    cash_flow = face * c_rate
    pv_principal = face / ((1 + m_rate) ** periods)
    pv_interest = sum([cash_flow / ((1 + m_rate) ** t) for t in range(1, periods + 1)])
    issue_price = pv_principal + pv_interest
    
    data = []
    book_value = issue_price
    
    data.append({
        "기간": 0,
        f"유효이자({int(m_rate*100)}%)": "",
        f"액면이자({int(c_rate*100)}%)": "",
        "상각액": "",
        "장부금액": f"{int(book_value):,}"
    })
    
    for t in range(1, periods + 1):
        start_bv = book_value
        interest_exp = start_bv * m_rate
        coupon = face * c_rate
        amort = interest_exp - coupon
        end_bv = start_bv + amort
        
        data.append({
            "기간": t,
            f"유효이자({int(m_rate*100)}%)": f"{int(round(interest_exp, 0)):,}",
            f"액면이자({int(c_rate*100)}%)": f"{int(round(coupon, 0)):,}",
            "상각액": f"{int(round(amort, 0)):,}",
            "장부금액": f"{int(round(end_bv, 0)):,}"
        })
        book_value = end_bv
        
    df = pd.DataFrame(data).set_index("기간")
    return issue_price, df

# =========================================================
# 4. 메인 UI
# =========================================================
st.title("☁️ Accoun-T Cloud")

with st.sidebar:
    st.header("Controller")
    menu = st.radio("메뉴 이동", ["학습 모드 (Student)", "관리자 모드 (Admin)"])
    st.divider()
    
    # DB 현황 (클라우드에서 가져옴)
    questions = get_all_questions()
    st.info(f"🔥 Firebase 연동 중\n등록된 문제: {len(questions)}개")

# [A] 학습 모드
if menu == "학습 모드 (Student)":
    tab1, tab2 = st.tabs(["🧪 이론 시뮬레이터", "🔥 기출 실전 풀이"])
    
    with tab1:
        st.subheader("사채(Bonds) 시뮬레이터")
        col_input, col_view = st.columns([1, 2])
        with col_input:
            face = st.number_input("액면금액", 100000, step=10000)
            crate = st.number_input("표시이자(%)", 5.0) / 100
            mrate = st.number_input("시장(유효)이자(%)", 8.0) / 100
            years = st.slider("만기", 1, 5, 3)
        with col_view:
            price, df = calculate_bond_schedule(face, crate, mrate, years)
            st.metric("발행금액", f"{int(price):,}원")
            st.table(df)

    with tab2:
        st.subheader("기출문제 풀이")
        if not questions:
            st.warning("등록된 문제가 없습니다.")
        else:
            q_map = {q['question_id']: f"[{q.get('exam_info',{}).get('year','-')}] {q['topic']}" for q in questions}
            sel_id = st.selectbox("문제 선택", list(q_map.keys()), format_func=lambda x: q_map[x])
            q_item = next(q for q in questions if q['question_id'] == sel_id)
            
            st.divider()
            c1, c2 = st.columns([1, 1])
            with c1:
                st.markdown(f"**Q. {q_item['topic']}**")
                st.markdown(q_item['content_markdown'])
                choices = q_item.get('choices', {})
                if choices:
                    opts = [f"{k}. {v}" for k, v in sorted(choices.items())]
                    st.radio("정답", opts, label_visibility="collapsed")
            with c2:
                with st.expander("💡 정답 및 해설"):
                    st.success(f"정답: {q_item.get('answer', '?')}")
                    if q_item.get('key_variables'):
                        st.json(q_item['key_variables'])

# [B] 관리자 모드
elif menu == "관리자 모드 (Admin)":
    st.header("🛠️ 클라우드 DB 관리")
    
    at1, at2 = st.tabs(["📥 문제 등록", "🗑️ 문제 관리"])
    
    with at1:
        st.markdown("Gemini JSON 코드를 붙여넣으세요. (자동으로 Cloud에 저장됨)")
        json_input = st.text_area("JSON Input", height=200)
        if st.button("서버에 저장"):
            try:
                new_items = json.loads(json_input)
                if not isinstance(new_items, list): new_items = [new_items]
                
                success_cnt = 0
                for item in new_items:
                    if save_question(item): success_cnt += 1
                
                st.success(f"{success_cnt}건 저장 완료!")
                st.balloons()
            except Exception as e:
                st.error(f"오류: {e}")

    with at2:
        st.markdown("등록된 문제 목록 (실시간 연동)")
        if questions:
            df_list = []
            for q in questions:
                df_list.append({
                    "ID": q['question_id'], 
                    "주제": q['topic'], 
                    "엔진": q.get('engine_type','-')
                })
            st.dataframe(pd.DataFrame(df_list), use_container_width=True)
            
            st.divider()
            del_id = st.selectbox("삭제할 문제 ID", [q['question_id'] for q in questions])
            if st.button("선택한 문제 삭제"):
                if delete_question(del_id):
                    st.success("삭제되었습니다.")
                    st.rerun()