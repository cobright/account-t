import streamlit as st
import pandas as pd
import json
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore

# =========================================================
# 1. 시스템 설정 및 Firebase 초기화
# =========================================================
st.set_page_config(page_title="Accoun-T Cloud", layout="wide", page_icon="☁️")

if "firestore_db" not in st.session_state:
    if not firebase_admin._apps:
        try:
            key_dict = dict(st.secrets["firestore"])
            if "private_key" in key_dict:
                key_dict["private_key"] = key_dict["private_key"].replace("\\n", "\n")
            
            cred = credentials.Certificate(key_dict)
            firebase_admin.initialize_app(cred)
        except Exception as e:
            st.error(f"🔥 Firebase 연결 실패: {e}")
            st.stop()
            
    st.session_state.firestore_db = firestore.client()

db = st.session_state.firestore_db

# =========================================================
# 2. 데이터 핸들링 함수 (CRUD)
# =========================================================
@st.cache_data(ttl=60)
def get_all_questions():
    """모든 문제 가져오기"""
    docs = db.collection("questions").stream()
    return [doc.to_dict() for doc in docs]

def save_question(question_data):
    """문제 저장 (Upsert)"""
    try:
        q_id = question_data['question_id']
        db.collection("questions").document(q_id).set(question_data)
        get_all_questions.clear()
        return True
    except Exception as e:
        st.error(f"저장 실패: {e}")
        return False

def update_solution(q_id, solution_steps):
    """[NEW] 해설(solution_steps)만 업데이트"""
    try:
        # 해당 문서의 solution_steps 필드만 수정 (merge=True 효과)
        db.collection("questions").document(q_id).update({
            "solution_steps": solution_steps
        })
        get_all_questions.clear()
        return True
    except Exception as e:
        st.error(f"해설 업데이트 실패: {e}")
        return False

def delete_question(q_id):
    """문제 삭제"""
    try:
        db.collection("questions").document(q_id).delete()
        get_all_questions.clear()
        return True
    except Exception as e:
        st.error(f"삭제 실패: {e}")
        return False

# =========================================================
# 3. PV 엔진 로직
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
    
    questions = get_all_questions()
    if questions:
        st.success(f"🔥 Firebase 연동됨 ({len(questions)}문제)")
    else:
        st.info("🔥 Firebase 연동됨 (데이터 없음)")

# ---------------------------------------------------------
# [A] 학습 모드 (Student)
# ---------------------------------------------------------
if menu == "학습 모드 (Student)":
    tab1, tab2 = st.tabs(["🧪 이론 시뮬레이터", "🔥 기출 실전 풀이"])
    
    with tab1:
        st.subheader("사채(Bonds) 시뮬레이터")
        col_input, col_view = st.columns([1, 2])
        with col_input:
            face = st.number_input("액면금액", value=100000, step=10000)
            crate = st.number_input("표시이자율(%)", value=5.0) / 100
            mrate = st.number_input("유효이자율(%)", value=8.0) / 100
            years = st.slider("만기(년)", 1, 5, 3)
        with col_view:
            price, df = calculate_bond_schedule(face, crate, mrate, years)
            m1, m2 = st.columns(2)
            m1.metric("발행금액", f"{int(price):,}원")
            m2.metric("할인/할증차금", f"{int(price-face):,}원")
            st.dataframe(df, use_container_width=True)

    with tab2:
        st.subheader("기출문제 풀이")
        if not questions:
            st.warning("문제가 없습니다.")
        else:
            q_map = {q['question_id']: f"[{q.get('exam_info',{}).get('year','-')}] {q['topic']}" for q in questions}
            sorted_ids = sorted(q_map.keys())
            sel_id = st.selectbox("문제 선택", sorted_ids, format_func=lambda x: q_map[x])
            q_item = next(q for q in questions if q['question_id'] == sel_id)
            
            st.divider()
            c1, c2 = st.columns([1.2, 0.8])
            with c1:
                st.markdown(f"#### Q. {q_item['topic']}")
                st.markdown(q_item['content_markdown'])
                choices = q_item.get('choices', {})
                if choices:
                    st.write("---")
                    opts = [f"{k}. {v}" for k, v in sorted(choices.items())]
                    st.radio("정답 선택", opts, label_visibility="collapsed")
            with c2:
                with st.expander("💡 정답 및 해설 확인"):
                    st.info(f"정답: **{q_item.get('answer', '?')}번**")
                    
                    if q_item.get('solution_steps'):
                        for step in q_item['solution_steps']:
                            st.markdown(f"**Step {step.get('step')}: {step.get('title')}**")
                            st.caption(step.get('content'))
                            st.divider()
                    else:
                        st.caption("해설이 아직 등록되지 않았습니다.")
                        
                if q_item.get('key_variables'):
                    st.success("🤖 **AI 추출 변수**")
                    st.json(q_item['key_variables'])

# ---------------------------------------------------------
# [B] 관리자 모드 (Admin)
# ---------------------------------------------------------
elif menu == "관리자 모드 (Admin)":
    st.header("🛠️ 클라우드 DB 관리 센터")
    
    # [NEW] 탭이 3개로 늘어났습니다.
    at1, at2, at3 = st.tabs(["📥 문제 등록", "📝 해설 등록/수정", "🗑️ 문제 관리"])
    
    # 1. 문제 등록
    with at1:
        st.markdown("Gemini가 변환해준 **문제 JSON**을 붙여넣으세요.")
        json_input = st.text_area("문제 JSON Input", height=200)
        if st.button("문제 저장"):
            try:
                new_items = json.loads(json_input)
                if not isinstance(new_items, list): new_items = [new_items]
                cnt = 0
                for item in new_items:
                    if save_question(item): cnt += 1
                st.success(f"{cnt}건 저장 완료!")
                st.rerun()
            except Exception as e:
                st.error(f"오류: {e}")

    # 2. [NEW] 해설 등록/수정 기능
    with at2:
        st.markdown("이미 등록된 문제에 **해설(Solution)**만 추가하거나 수정합니다.")
        
        if questions:
            # 문제 선택
            q_map = {q['question_id']: f"[{q.get('exam_info',{}).get('year','-')}] {q['topic']}" for q in questions}
            sorted_ids = sorted(q_map.keys())
            target_id = st.selectbox("해설을 달 문제를 선택하세요", sorted_ids, format_func=lambda x: q_map[x])
            
            # 선택된 문제 정보 보여주기 (확인용)
            target_q = next(q for q in questions if q['question_id'] == target_id)
            with st.expander("문제 내용 확인 (Click)"):
                st.markdown(target_q['content_markdown'])
            
            # 기존 해설이 있다면 보여주기
            if target_q.get('solution_steps'):
                st.info("ℹ️ 이미 등록된 해설이 있습니다. 아래 입력하면 덮어씌워집니다.")
                st.json(target_q['solution_steps'])

            # 해설 입력창
            st.markdown("👇 Gemini가 생성한 **해설 JSON (리스트 형태)**을 붙여넣으세요.")
            sol_input = st.text_area("해설 JSON Input", height=200, placeholder='[\n  {"step": 1, "title": "분석", "content": "내용..."},\n  ...\n]')
            
            if st.button("해설 업데이트"):
                try:
                    sol_data = json.loads(sol_input)
                    if isinstance(sol_data, list):
                        if update_solution(target_id, sol_data):
                            st.success("✅ 해설이 성공적으로 등록되었습니다!")
                            st.rerun()
                    else:
                        st.error("JSON 형식이 리스트([...])가 아닙니다.")
                except json.JSONDecodeError:
                    st.error("올바른 JSON 형식이 아닙니다.")
                except Exception as e:
                    st.error(f"오류: {e}")
        else:
            st.warning("등록된 문제가 없어 해설을 달 수 없습니다.")

    # 3. 문제 삭제
    with at3:
        if questions:
            df_list = []
            for q in questions:
                info = q.get('exam_info', {})
                df_list.append({
                    "ID": q['question_id'], 
                    "연도": info.get('year', '-'),
                    "주제": q['topic'], 
                    "해설여부": "O" if q.get('solution_steps') else "X"
                })
            st.dataframe(pd.DataFrame(df_list), use_container_width=True)
            
            del_id = st.selectbox("삭제할 문제 ID", [q['question_id'] for q in questions])
            if st.button("🗑️ 영구 삭제"):
                if delete_question(del_id):
                    st.success("삭제되었습니다.")
                    st.rerun()