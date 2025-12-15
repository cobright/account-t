import streamlit as st
import pandas as pd
import json
import firebase_admin
from firebase_admin import credentials, firestore
import google.generativeai as genai

# =========================================================
# 1. 시스템 설정 및 초기화
# =========================================================
st.set_page_config(page_title="Accoun-T Cloud", layout="wide", page_icon="☁️")

# (1) Firebase 초기화
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

# (2) Gemini API 초기화
GEMINI_AVAILABLE = False
if "gemini" in st.secrets:
    try:
        genai.configure(api_key=st.secrets["gemini"]["api_key"])
        GEMINI_AVAILABLE = True
    except:
        pass

# =========================================================
# 2. CRUD 및 로직 함수
# =========================================================
@st.cache_data(ttl=60)
def get_all_questions():
    """모든 문제 가져오기"""
    try:
        docs = db.collection("questions").stream()
        return [doc.to_dict() for doc in docs]
    except Exception as e:
        st.error(f"DB 읽기 오류: {e}")
        return []

def save_question_batch(items):
    """문제 대량 등록"""
    batch = db.batch()
    count = 0
    for item in items:
        if 'question_id' in item:
            doc_ref = db.collection("questions").document(item['question_id'])
            batch.set(doc_ref, item)
            count += 1
    batch.commit()
    get_all_questions.clear()
    return count

def update_solution_batch(items):
    """해설 대량 업데이트 (키 이름 호환성 처리 포함)"""
    batch = db.batch()
    count = 0
    
    # 현재 DB에 있는 유효한 ID 목록 가져오기
    valid_ids = {q['question_id'] for q in get_all_questions() if 'question_id' in q}
    
    for item in items:
        q_id = item.get('question_id')
        
        # 사용자가 'steps'라고 썼든 'solution_steps'라고 썼든 다 받아줌
        steps = item.get('solution_steps') or item.get('steps')
        
        if q_id and steps and (q_id in valid_ids):
            doc_ref = db.collection("questions").document(q_id)
            batch.update(doc_ref, {"solution_steps": steps})
            count += 1
    
    if count > 0:
        batch.commit()
        get_all_questions.clear()
    return count

def generate_ai_solution(question_data):
    """AI 실시간 해설 생성"""
    if not GEMINI_AVAILABLE: return None
    try:
        model = genai.GenerativeModel('gemini-2.5-flash')
        prompt = f"""
        당신은 회계학 강사입니다. 다음 문제의 '단계별 해설'을 JSON으로 작성하세요.
        [문제] {question_data.get('content_markdown', '')}
        [출력형식 JSON]
        [
          {{"step": 1, "title": "분석", "content": "..."}},
          {{"step": 2, "title": "계산", "content": "..."}}
        ]
        """
        resp = model.generate_content(prompt)
        text = resp.text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except Exception as e:
        st.error(f"AI 호출 오류: {e}")
        return None

def calculate_bond_schedule(face, c_rate, m_rate, periods):
    cash_flow = face * c_rate
    pv_principal = face / ((1 + m_rate) ** periods)
    pv_interest = sum([cash_flow / ((1 + m_rate) ** t) for t in range(1, periods + 1)])
    issue_price = pv_principal + pv_interest
    
    data = []
    book_value = issue_price
    data.append({"기간": 0, "유효이자": "", "액면이자": "", "상각액": "", "장부금액": f"{int(book_value):,}"})
    
    for t in range(1, periods + 1):
        start_bv = book_value
        interest_exp = start_bv * m_rate
        coupon = face * c_rate
        amort = interest_exp - coupon
        end_bv = start_bv + amort
        data.append({
            "기간": t,
            "유효이자": f"{int(interest_exp):,}",
            "액면이자": f"{int(coupon):,}",
            "상각액": f"{int(amort):,}",
            "장부금액": f"{int(end_bv):,}"
        })
        book_value = end_bv
    return issue_price, pd.DataFrame(data).set_index("기간")

# =========================================================
# 3. 메인 UI
# =========================================================
st.title("☁️ Accoun-T Cloud")

with st.sidebar:
    st.header("🔍 학습 필터")
    all_data = get_all_questions()
    
    # 엔진 필터
    engine_list = sorted(list(set([q.get('engine_type', '기타') for q in all_data])))
    selected_engines = st.multiselect("엔진 선택 (Engine)", engine_list, default=engine_list)
    
    st.divider()
    menu = st.radio("메뉴 이동", ["학습 모드 (Student)", "관리자 모드 (Admin)"])
    
    # 필터링
    if selected_engines:
        filtered_questions = [q for q in all_data if q.get('engine_type', '기타') in selected_engines]
    else:
        filtered_questions = all_data

    st.caption(f"총 {len(all_data)}문제 중 {len(filtered_questions)}문제 표시")

# ---------------------------------------------------------
# [A] 학습 모드
# ---------------------------------------------------------
if menu == "학습 모드 (Student)":
    tab1, tab2 = st.tabs(["🧪 이론 시뮬레이터", "🔥 기출 실전 풀이"])
    
    with tab1:
        st.subheader("사채(Bonds) 시뮬레이터")
        c1, c2 = st.columns([1, 2])
        with c1:
            face = st.number_input("액면", 100000, step=10000)
            crate = st.number_input("표시이자(%)", 5.0)/100
            mrate = st.number_input("유효이자(%)", 8.0)/100
            years = st.slider("만기", 1, 5, 3)
        with c2:
            p, df = calculate_bond_schedule(face, crate, mrate, years)
            st.metric("발행가액", f"{int(p):,}원")
            st.dataframe(df, use_container_width=True)

    with tab2:
        st.subheader("기출문제 풀이")
        if not filtered_questions:
            st.warning("선택된 주제의 문제가 없습니다.")
        else:
            # 문제 선택
            q_map = {}
            for q in filtered_questions:
                qid = q.get('question_id', 'unknown')
                topic = q.get('topic', '제목없음')
                engine = q.get('engine_type', '-')
                q_map[qid] = f"[{engine}] {topic} ({qid})"
            
            sorted_ids = sorted(q_map.keys())
            sel_id = st.selectbox("문제 선택", sorted_ids, format_func=lambda x: q_map[x])
            
            # 선택된 문제 데이터 가져오기
            q_item = next((q for q in filtered_questions if q['question_id'] == sel_id), None)
            
            if q_item:
                st.divider()
                c_q, c_a = st.columns([1.2, 0.8])
                
                # [좌측] 문제
                with c_q:
                    st.markdown(f"#### Q. {q_item.get('topic', '')}")
                    st.markdown(q_item.get('content_markdown', ''))
                    
                    choices = q_item.get('choices')
                    if choices:
                        st.write("---")
                        # choices가 리스트인지 딕셔너리인지 확인하여 처리
                        if isinstance(choices, dict):
                            opts = [f"{k}. {v}" for k, v in sorted(choices.items())]
                        elif isinstance(choices, list):
                            opts = choices
                        else:
                            opts = []
                        st.radio("정답 선택", opts, label_visibility="collapsed")
                
                # [우측] 해설
                with c_a:
                    st.markdown("#### 💡 AI 튜터")
                    
                    # 해설 데이터 확인 (solution_steps 또는 steps)
                    sol_steps = q_item.get('solution_steps') or q_item.get('steps')
                    
                    if sol_steps and isinstance(sol_steps, list):
                        with st.expander("해설 보기", expanded=True):
                            st.success(f"정답: {q_item.get('answer', '?')}번")
                            
                            for step in sol_steps:
                                # [수정됨] KeyError 방지를 위해 .get() 사용
                                if isinstance(step, dict):
                                    s_step = step.get('step', '-')
                                    s_title = step.get('title', '')
                                    s_content = step.get('content', '')
                                    
                                    st.markdown(f"**Step {s_step}: {s_title}**")
                                    st.caption(s_content)
                                    st.divider()
                    else:
                        st.info("아직 등록된 해설이 없습니다.")
                        if GEMINI_AVAILABLE:
                            if st.button("🤖 AI에게 지금 풀이 요청하기"):
                                with st.spinner("AI가 분석 중..."):
                                    new_sol = generate_ai_solution(q_item)
                                    if new_sol:
                                        db.collection("questions").document(sel_id).update({"solution_steps": new_sol})
                                        st.success("해설 생성 완료! 새로고침합니다.")
                                        st.rerun()
                        else:
                            st.caption("AI 기능을 사용하려면 API Key 설정이 필요합니다.")

# ---------------------------------------------------------
# [B] 관리자 모드
# ---------------------------------------------------------
elif menu == "관리자 모드 (Admin)":
    st.header("🛠️ 통합 데이터 관리자")
    t1, t2, t3 = st.tabs(["📥 문제 일괄 등록", "📝 해설 일괄 등록", "🗑️ 데이터 관리"])
    
    # 1. 문제 등록
    with t1:
        st.info("문제 JSON 리스트를 붙여넣으세요.")
        q_json = st.text_area("Question JSON", height=200)
        if st.button("문제 업로드"):
            try:
                data = json.loads(q_json)
                if not isinstance(data, list): data = [data]
                cnt = save_question_batch(data)
                st.success(f"{cnt}건 업로드 완료!")
            except Exception as e:
                st.error(f"오류: {e}")

    # 2. 해설 등록 (유연함: steps 키도 허용)
    with t2:
        st.success("JSON 안에 'question_id'만 있으면 알아서 찾아가서 붙습니다.")
        s_json = st.text_area("Solution JSON", height=200)
        if st.button("해설 합체 (Merge)"):
            try:
                data = json.loads(s_json)
                if not isinstance(data, list): data = [data]
                cnt = update_solution_batch(data)
                if cnt > 0:
                    st.success(f"총 {cnt}건의 해설 업데이트 완료!")
                    st.rerun()
                else:
                    st.warning("일치하는 문제 ID가 없거나 데이터 형식이 맞지 않습니다.")
            except Exception as e:
                st.error(f"오류: {e}")

    # 3. 삭제
    with t3:
        if all_data:
            df = pd.DataFrame(all_data)
            # 없는 컬럼 에러 방지
            cols = [c for c in ['question_id', 'topic', 'engine_type'] if c in df.columns]
            st.dataframe(df[cols], use_container_width=True)
            
            if 'question_id' in df.columns:
                d_id = st.selectbox("삭제할 ID", df['question_id'])
                if st.button("영구 삭제"):
                    db.collection("questions").document(d_id).delete()
                    get_all_questions.clear()
                    st.rerun()