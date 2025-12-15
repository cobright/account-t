import streamlit as st
import pandas as pd
import json
import google.generativeai as genai
from pathlib import Path

# =========================================================
# 1. 설정 및 데이터 로딩
# =========================================================
st.set_page_config(page_title="Accoun-T Master", layout="wide", page_icon="🎓")

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "db" / "question_db.json"
CURRICULUM_PATH = BASE_DIR / "db" / "curriculum.json"

# 세션 상태 초기화 (API 키 저장 등)
if "api_key" not in st.session_state:
    st.session_state.api_key = ""

@st.cache_data
def load_data():
    questions = []
    curriculum = []
    if DB_PATH.exists():
        with open(DB_PATH, "r", encoding="utf-8") as f:
            questions = json.load(f)
    if CURRICULUM_PATH.exists():
        with open(CURRICULUM_PATH, "r", encoding="utf-8") as f:
            curriculum = json.load(f)
    return questions, curriculum

def save_solution_to_db(q_id, solution_steps):
    """생성된 AI 풀이를 JSON DB에 업데이트 및 저장"""
    try:
        with open(DB_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        # 해당 문제 찾아서 solution_steps 추가
        for q in data:
            if q['question_id'] == q_id:
                q['solution_steps'] = solution_steps
                break
        
        with open(DB_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        # 캐시 비우기 (새로고침 시 반영되도록)
        load_data.clear()
        return True
    except Exception as e:
        st.error(f"DB 저장 실패: {e}")
        return False

# =========================================================
# 2. 로직 함수 (계산기 & AI 솔버)
# =========================================================
def calculate_bond_schedule(face, c_rate, m_rate, periods):
    # --- 1. 계산 로직 (숫자 다루기) ---
    cash_flow = face * c_rate
    pv_principal = face / ((1 + m_rate) ** periods)
    pv_interest = sum([cash_flow / ((1 + m_rate) ** t) for t in range(1, periods + 1)])
    issue_price = pv_principal + pv_interest
    
    data = []
    book_value = issue_price
    
    # 기간 0 (문자열로 미리 포맷팅)
    data.append({
        "기간": 0,
        f"유효이자({int(m_rate*100)}%)": "",   # 빈칸
        f"액면이자({int(c_rate*100)}%)": "",   # 빈칸
        "상각액": "",                         # 빈칸
        "장부금액": f"{int(book_value):,}"    # 콤마 찍은 문자열
    })
    
    for t in range(1, periods + 1):
        start_bv = book_value
        interest_exp = start_bv * m_rate
        coupon = face * c_rate
        amort = interest_exp - coupon
        end_bv = start_bv + amort
        
        data.append({
            "기간": t,
            f"유효이자({int(m_rate*100)}%)": f"{int(round(interest_exp, 0)):,}", # 콤마 포맷팅
            f"액면이자({int(c_rate*100)}%)": f"{int(round(coupon, 0)):,}",
            "상각액": f"{int(round(amort, 0)):,}",
            "장부금액": f"{int(round(end_bv, 0)):,}"
        })
        book_value = end_bv
        
    # --- 2. 출력용 데이터프레임 ---
    df = pd.DataFrame(data).set_index("기간")
    
    return issue_price, df    

def generate_ai_solution(api_key, question_data):
    """Gemini API를 호출하여 단계별 풀이 생성"""
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.5-flash')
    
    prompt = f"""
    당신은 친절한 회계학 1타 강사입니다. 아래 문제를 보고 수험생이 이해하기 쉬운 '단계별 풀이'를 작성해주세요.
    
    [문제]
    {question_data['content_markdown']}
    
    [요청사항]
    반드시 아래 JSON 형식으로만 답변하세요. (마크다운 코드블록 없이 순수 JSON)
    
    [
      {{"step": 1, "title": "문제 분석 및 출제 의도", "content": "이 문제는 사채의... 를 묻고 있습니다."}},
      {{"step": 2, "title": "핵심 계산 과정", "content": "1. 유효이자 = ... \\n 2. 상각액 = ..."}},
      {{"step": 3, "title": "최종 정답 도출", "content": "따라서 정답은..."}}
    ]
    """
    
    try:
        response = model.generate_content(prompt)
        text = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except Exception as e:
        st.error(f"AI 호출 중 오류 발생: {e}")
        return None

# =========================================================
# 3. 메인 UI
# =========================================================
questions_data, curriculum_data = load_data()

with st.sidebar:
    st.title("🎓 Accoun-T Campus")
    
    # API 키 입력 (보안을 위해 비밀번호 형태)
    api_input = st.text_input("Gemini API Key", type="password", placeholder="AI 풀이 생성 시 필요")
    if api_input:
        st.session_state.api_key = api_input
        
    mode = st.radio("학습 모드", ["정규 커리큘럼 (이론)", "자율 학습 (연습/기출)"])
    st.divider()
    st.caption(f"📚 커리큘럼: {len(curriculum_data)}개 | 💾 기출문제: {len(questions_data)}개")

# ---------------------------------------------------------
# MODE A: 정규 커리큘럼 (기존 코드 유지)
# ---------------------------------------------------------
if mode == "정규 커리큘럼 (이론)":
    st.header("📖 개념 완성 코스")
    # ... (이전 코드와 동일하므로 생략 없이 필요한 부분만 기술) ...
    # (사용자 편의를 위해 이 부분은 이전 턴의 코드를 그대로 두시면 됩니다. 
    #  혹시 코드가 길어 생략되었다면 이전 턴의 '정규 커리큘럼' 부분 로직을 그대로 사용하세요.)
    
    course_titles = [c['title'] for c in curriculum_data]
    if not course_titles:
        st.warning("커리큘럼 데이터가 없습니다.")
    else:
        sel_course = st.selectbox("수강할 코스", course_titles)
        course = next(c for c in curriculum_data if c['title'] == sel_course)
        st.markdown(f"> {course['description']}")
        
        ch_titles = [f"{ch['step']}. {ch['title']}" for ch in course['chapters']]
        sel_ch_str = st.radio("목차", ch_titles, horizontal=True)
        chapter = course['chapters'][ch_titles.index(sel_ch_str)]
        preset = chapter['preset_values']
        
        c_txt, c_sim = st.columns([1, 1.2])
        with c_txt:
            st.subheader(chapter['title'])
            st.markdown(chapter['content_markdown'])
        with c_sim:
            st.subheader("🖥️ Simulator")
            # 시뮬레이터 UI
            p_face = st.number_input("액면금액", value=preset['face_value'], step=10000)
            c1, c2 = st.columns(2)
            with c1: p_crate = st.number_input("표시이자(%)", value=preset['coupon_rate']*100) / 100
            with c2: p_mrate = st.number_input("시장이자(%)", value=preset['market_rate']*100) / 100
            p_years = st.slider("만기", 1, 5, preset['years'])
            
            # [수정] 시뮬레이터 출력 부분 (Tab 1, Curriculum 등 모든 곳에 적용)

            # 1. 계산 실행
            price, df_display = calculate_bond_schedule(p_face, p_crate, p_mrate, p_years)

            # 2. 결과 카드 (발행가액 등)
            # m1, m2 = st.columns(2)
            # m1.metric("발행금액 (PV)", f"{int(price):,}원")
            # m2.metric("할인/할증 차금", f"{int(price - p_face):,}원")

            # 3. 그래프 (선택사항 - 흐름 보기에 좋으므로 유지 추천)
            # (그래프 그릴 땐 df_display 대신 숫자가 있는 원본 df가 필요하므로, 위 함수에서 df와 df_display 둘 다 리턴받는 게 좋음.
            #  하지만 간단히 하려면 df_display에서 '장부금액'만 뽑아서 그려도 됨)
            # st.line_chart(df_display['장부금액'])

            # 4. [핵심] 상각표 출력 (시험지 스타일)
            st.subheader("📋 상각표 (Amortization Schedule)")
            st.dataframe(
                df_display,
                use_container_width=True,
                column_config={
                    # 각 컬럼별로 천 단위 콤마 포맷 지정 (문자열 빈칸이 섞여 있어도 작동하도록 NumberColumn 아님 TextColumn으로 인식될 수 있음)
                    # 팁: df_display가 이미 object 타입이므로, 데이터 자체가 깔끔해야 함.
                    # 가장 확실한 방법은 위 함수 calculate_bond_schedule에서 포맷팅까지 끝내는 것임.
                }
            )

# ---------------------------------------------------------
# MODE B: 자율 학습 (AI 풀이 기능 탑재)
# ---------------------------------------------------------
elif mode == "자율 학습 (연습/기출)":
    st.header("🏋️ 자율 트레이닝 센터")
    tab_exam, tab_drill = st.tabs(["🔥 기출 실전", "⚡ 기본 훈련"]) # 순서 살짝 변경
    
    with tab_exam:
        # 엔진 필터링
        engine_list = list(set([q['engine_type'] for q in questions_data])) if questions_data else []
        sel_eng = st.selectbox("엔진 필터", engine_list) if engine_list else None
        
        filtered_q = [q for q in questions_data if q['engine_type'] == sel_eng] if sel_eng else questions_data
        
        if not filtered_q:
            st.warning("등록된 문제가 없습니다.")
        else:
            # 문제 선택
            q_map = {q['question_id']: f"[{q['difficulty']}] {q['topic']}" for q in filtered_q}
            sel_qid = st.selectbox("문제 선택", list(q_map.keys()), format_func=lambda x: q_map[x])
            q_data = next(q for q in filtered_q if q['question_id'] == sel_qid)
            
            st.divider()
            col_q, col_sol = st.columns([1, 1])
            
            # [좌측] 문제 영역
            with col_q:
                st.markdown(f"### Q. {q_data['topic']}")
                st.markdown(q_data['content_markdown'])
                st.write("---")
                
                # [수정됨] 보기 데이터가 있으면 가져와서 표시
                choices = q_data.get('choices', {})
                
                if choices:
                    # 딕셔너리를 "번호. 내용" 형식의 리스트로 변환 (예: "1. 50,000원")
                    # 키(key) 순서대로 정렬하여 리스트 생성
                    options = [f"{k}. {v}" for k, v in sorted(choices.items())]
                else:
                    # 데이터가 없을 경우 기본값 표시
                    options = ["1", "2", "3", "4", "5"]

                # 라디오 버튼 생성
                user_ans_str = st.radio("정답을 선택하세요", options)
                
                if st.button("정답 확인"):
                    # DB상의 정답 번호 (문자열로 변환)
                    correct_ans = str(q_data.get('answer'))
                    
                    # 사용자가 선택한 문자열에서 번호만 추출 ("1. 50,000원" -> "1")
                    selected_no = user_ans_str.split('.')[0].strip()
                    
                    if selected_no == correct_ans:
                        st.success(f"🎉 정답입니다! ({correct_ans}번)")
                        st.balloons() # 정답 축하 효과
                    else:
                        st.error(f"❌ 틀렸습니다. 정답은 **{correct_ans}번** 입니다")

            # [우측] AI 풀이 영역
            with col_sol:
                st.markdown("### 💡 AI 튜터의 해설")
                
                # 1. DB에 이미 풀이가 있는지 확인 (캐싱 체크)
                if "solution_steps" in q_data and q_data['solution_steps']:
                    st.success("✅ 저장된 풀이를 불러왔습니다.")
                    steps = q_data['solution_steps']
                    for step in steps:
                        with st.expander(f"STEP {step['step']}: {step['title']}"):
                            st.markdown(step['content'])
                            
                # 2. 풀이가 없으면 AI 호출 버튼 표시
                else:
                    st.info("아직 저장된 해설이 없습니다.")
                    if st.button("🤖 AI에게 단계별 풀이 요청하기"):
                        if not st.session_state.api_key:
                            st.error("사이드바에 API Key를 먼저 입력해주세요!")
                        else:
                            with st.spinner("Gemini가 문제를 분석하고 해설을 작성 중입니다..."):
                                # AI 호출
                                solution = generate_ai_solution(st.session_state.api_key, q_data)
                                if solution:
                                    # DB 저장
                                    if save_solution_to_db(q_data['question_id'], solution):
                                        st.rerun() # 화면 새로고침해서 풀이 표시