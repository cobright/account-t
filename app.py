import streamlit as st
import pandas as pd
import json
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore

# =========================================================
# 1. 시스템 설정 및 Firebase 초기화 (Secrets 연동)
# =========================================================
st.set_page_config(page_title="Accoun-T Cloud", layout="wide", page_icon="☁️")

# 세션 상태에 DB 연결 객체 저장 (새로고침 시 재연결 방지)
if "firestore_db" not in st.session_state:
    # 이미 초기화된 앱이 있는지 확인
    if not firebase_admin._apps:
        try:
            # Streamlit Secrets에서 키 가져오기
            key_dict = dict(st.secrets["firestore"])
            
            # [중요] TOML 파일 특성상 줄바꿈(\n)이 문자로 인식될 수 있어 변환 처리
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
# ttl=60: 60초 동안은 DB를 다시 읽지 않고 캐시된 데이터 사용 (속도 향상 & 비용 절감)
@st.cache_data(ttl=60)
def get_all_questions():
    """모든 문제 가져오기"""
    docs = db.collection("questions").stream()
    return [doc.to_dict() for doc in docs]

def save_question(question_data):
    """문제 저장 또는 수정 (Upsert)"""
    try:
        q_id = question_data['question_id']
        db.collection("questions").document(q_id).set(question_data)
        get_all_questions.clear() # 데이터가 바뀌었으니 캐시 초기화
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
# 3. PV 엔진 로직 (시험지 스타일 상각표)
# =========================================================
def calculate_bond_schedule(face, c_rate, m_rate, periods):
    # 1. 발행금액(PV) 계산
    cash_flow = face * c_rate
    pv_principal = face / ((1 + m_rate) ** periods)
    pv_interest = sum([cash_flow / ((1 + m_rate) ** t) for t in range(1, periods + 1)])
    issue_price = pv_principal + pv_interest
    
    data = []
    book_value = issue_price
    
    # 기간 0 (발행 직후) - 빈칸 처리 및 콤마 포맷팅
    data.append({
        "기간": 0,
        f"유효이자({int(m_rate*100)}%)": "",
        f"액면이자({int(c_rate*100)}%)": "",
        "상각액": "",
        "장부금액": f"{int(book_value):,}"
    })
    
    # 기간 1 ~ n
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
# 4. 메인 UI 구성
# =========================================================
st.title("☁️ Accoun-T Cloud")

# 사이드바 컨트롤러
with st.sidebar:
    st.header("Controller")
    menu = st.radio("메뉴 이동", ["학습 모드 (Student)", "관리자 모드 (Admin)"])
    st.divider()
    
    # DB 연결 상태 표시
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
    
    # Tab 1: 사채 시뮬레이터
    with tab1:
        st.subheader("사채(Bonds) 시뮬레이터")
        st.caption("시험지 스타일의 상각표를 자동으로 생성합니다.")
        
        col_input, col_view = st.columns([1, 2])
        
        with col_input:
            face = st.number_input("액면금액", value=100000, step=10000)
            crate = st.number_input("표시이자율(%)", value=5.0) / 100
            mrate = st.number_input("유효이자율(%)", value=8.0) / 100
            years = st.slider("만기(년)", 1, 5, 3)
            
        with col_view:
            price, df = calculate_bond_schedule(face, crate, mrate, years)
            
            # 결과 요약 카드
            m1, m2 = st.columns(2)
            m1.metric("발행금액 (PV)", f"{int(price):,}원")
            m2.metric("할인/할증차금", f"{int(price-face):,}원")
            
            # 상각표 출력
            st.dataframe(df, use_container_width=True)

    # Tab 2: 기출문제 풀이
    with tab2:
        st.subheader("기출문제 풀이")
        
        if not questions:
            st.warning("등록된 문제가 없습니다. 관리자 모드에서 문제를 추가해주세요.")
        else:
            # 문제 선택 박스 (ID 대신 '연도+주제'로 표시)
            q_map = {q['question_id']: f"[{q.get('exam_info',{}).get('year','-')}] {q['topic']}" for q in questions}
            # 데이터 정렬 (ID순)
            sorted_ids = sorted(q_map.keys())
            
            sel_id = st.selectbox("문제 선택", sorted_ids, format_func=lambda x: q_map[x])
            q_item = next(q for q in questions if q['question_id'] == sel_id)
            
            st.divider()
            
            # 문제 화면 분할
            c1, c2 = st.columns([1.2, 0.8])
            
            with c1:
                st.markdown(f"#### Q. {q_item['topic']}")
                st.markdown(q_item['content_markdown'])
                
                # 보기 출력 (딕셔너리 -> 리스트 변환)
                choices = q_item.get('choices', {})
                if choices:
                    st.write("---")
                    opts = [f"{k}. {v}" for k, v in sorted(choices.items())]
                    st.radio("정답을 선택하세요", opts, label_visibility="collapsed")
            
            with c2:
                # 정답 및 해설 (Expander)
                with st.expander("💡 정답 및 해설 확인"):
                    st.info(f"정답: **{q_item.get('answer', '?')}번**")
                    
                    # AI 해설이 있으면 표시
                    if q_item.get('solution_steps'):
                        for step in q_item['solution_steps']:
                            st.markdown(f"**Step {step.get('step')}: {step.get('title')}**")
                            st.caption(step.get('content'))
                            st.divider()
                    else:
                        st.caption("등록된 상세 해설이 없습니다.")
                        
                # 시뮬레이터 연동 데이터
                if q_item.get('key_variables'):
                    st.success("🤖 **AI 추출 변수**")
                    st.json(q_item['key_variables'])
                    st.caption("👈 왼쪽 시뮬레이터에 이 값을 넣어보세요!")

# ---------------------------------------------------------
# [B] 관리자 모드 (Admin)
# ---------------------------------------------------------
elif menu == "관리자 모드 (Admin)":
    st.header("🛠️ 클라우드 DB 관리 센터")
    
    at1, at2 = st.tabs(["📥 문제 등록 (Batch)", "🗑️ 문제 관리 (Delete)"])
    
    # [기능 1] JSON 등록
    with at1:
        st.markdown("""
        **Gemini가 변환해준 JSON 코드를 여기에 붙여넣으세요.**
        (단일 객체 `{}` 또는 리스트 `[{}]` 모두 가능)
        """)
        json_input = st.text_area("JSON Input", height=300)
        
        if st.button("🚀 클라우드 DB에 전송"):
            if not json_input.strip():
                st.warning("내용을 입력해주세요.")
            else:
                try:
                    new_items = json.loads(json_input)
                    # 리스트가 아니면 리스트로 감싸기
                    if not isinstance(new_items, list):
                        new_items = [new_items]
                    
                    success_cnt = 0
                    with st.status("데이터 업로드 중...") as status:
                        for item in new_items:
                            if 'question_id' in item:
                                save_question(item)
                                success_cnt += 1
                        status.update(label="업로드 완료!", state="complete", expanded=False)
                    
                    st.success(f"총 {success_cnt}건이 저장되었습니다.")
                    st.balloons()
                    
                except json.JSONDecodeError:
                    st.error("JSON 형식이 올바르지 않습니다.")
                except Exception as e:
                    st.error(f"오류 발생: {e}")

    # [기능 2] 문제 삭제
    with at2:
        st.markdown("등록된 문제 현황")
        if questions:
            # 요약표 생성
            df_list = []
            for q in questions:
                info = q.get('exam_info', {})
                df_list.append({
                    "ID": q['question_id'], 
                    "연도": info.get('year', '-'),
                    "주제": q['topic'], 
                    "엔진": q.get('engine_type','-')
                })
            
            st.dataframe(pd.DataFrame(df_list), use_container_width=True)
            
            st.divider()
            col_del1, col_del2 = st.columns([3, 1])
            with col_del1:
                del_id = st.selectbox("삭제할 문제 선택", [q['question_id'] for q in questions])
            with col_del2:
                st.write("") # 줄맞춤용
                st.write("")
                if st.button("🗑️ 영구 삭제"):
                    if delete_question(del_id):
                        st.success(f"{del_id} 삭제 완료.")
                        st.rerun()
        else:
            st.info("데이터가 없습니다.")