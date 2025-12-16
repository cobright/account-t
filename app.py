import streamlit as st
import pandas as pd
import json
import firebase_admin
from firebase_admin import credentials, firestore
import google.generativeai as genai
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, DataReturnMode

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
# 2. Simulator Engine
# =========================================================
class Simulators:
    @staticmethod
    def bond_basic(face, crate, mrate, periods):
        cash_flow = face * crate
        pv_principal = face / ((1 + mrate) ** periods)
        pv_interest = sum([cash_flow / ((1 + mrate) ** t) for t in range(1, periods + 1)])
        price = pv_principal + pv_interest
        
        data = []
        book_value = price
        data.append({"기간": 0, "유효이자": "-", "표시이자": "-", "상각액": "-", "장부금액": f"{int(book_value):,}"})
        
        for t in range(1, periods + 1):
            ie = book_value * mrate
            cp = face * crate
            am = ie - cp
            book_value += am
            data.append({
                "기간": t,
                "유효이자": f"{int(ie):,}", "표시이자": f"{int(cp):,}",
                "상각액": f"{int(am):,}", "장부금액": f"{int(book_value):,}"
            })
        return int(price), pd.DataFrame(data).set_index("기간")

    @staticmethod
    def depreciation(cost, residual, life, method, rate=None):
        data = []
        book_value = cost
        data.append({"연도": 0, "기초장부": "-", "상각비": "-", "기말장부": f"{int(cost):,}"})

        for t in range(1, life + 1):
            start_bv = book_value
            dep_expense = 0
            if method == "SL":
                dep_expense = (cost - residual) / life
            elif method == "DB":
                if t == life: dep_expense = start_bv - residual
                else: dep_expense = start_bv * (rate if rate else (1 - (residual/cost)**(1/life)))
            elif method == "SYD":
                syd = life * (life + 1) / 2
                dep_expense = (cost - residual) * ((life - t + 1) / syd)

            book_value -= dep_expense
            data.append({
                "연도": t, "기초장부": f"{int(start_bv):,}",
                "상각비": f"{int(dep_expense):,}", "기말장부": f"{int(book_value):,}"
            })
        return pd.DataFrame(data).set_index("연도")

    @staticmethod
    def inventory_fifo(base_qty, base_price, buy_qty, buy_price, sell_qty):
        cogs = 0
        rem_base = base_qty
        rem_buy = buy_qty
        
        sold_from_base = min(sell_qty, rem_base)
        cogs += sold_from_base * base_price
        rem_base -= sold_from_base
        
        sold_from_buy = min(sell_qty - sold_from_base, rem_buy)
        cogs += sold_from_buy * buy_price
        rem_buy -= sold_from_buy
        
        ending = (rem_base * base_price) + (rem_buy * buy_price)
        return cogs, ending, rem_base, rem_buy

# =========================================================
# 3. Data Logic
# =========================================================
@st.cache_data(ttl=60)
def load_courses():
    try:
        docs = db.collection("courses").stream()
        return [doc.to_dict() for doc in docs]
    except: return []

@st.cache_data(ttl=60)
def load_questions():
    try:
        docs = db.collection("questions").stream()
        return [doc.to_dict() for doc in docs]
    except: return []

def find_related_questions(keywords, all_questions):
    if not keywords: return []
    results = []
    for q in all_questions:
        search_text = (q.get('topic', '') + q.get('content_markdown', '')).lower()
        if any(k.lower() in search_text for k in keywords):
            results.append(q)
    return results

def save_json_batch(collection_name, items, id_field):
    batch = db.batch()
    count = 0
    for item in items:
        if id_field in item:
            doc_ref = db.collection(collection_name).document(str(item[id_field]))
            batch.set(doc_ref, item)
            count += 1
    batch.commit()
    return count

# =========================================================
# 4. UI Layout
# =========================================================
st.title("☁️ Accoun-T Cloud")

with st.sidebar:
    st.header("Controller")
    mode = st.radio("모드 선택", ["👨‍🎓 학습 모드 (Student)", "🛠️ 관리자 모드 (Admin)"])
    st.divider()
    
    selected_course = None
    if mode == "👨‍🎓 학습 모드 (Student)":
        courses = load_courses()
        if courses:
            engines = sorted(list(set([c['engine_type'] for c in courses])))
            sel_engine = st.selectbox("엔진 (Engine)", engines)
            engine_courses = [c for c in courses if c['engine_type'] == sel_engine]
            course_map = {c['course_id']: c['title'] for c in engine_courses}
            sel_course_id = st.selectbox("학습 주제 (Topic)", list(course_map.keys()), format_func=lambda x: course_map[x])
            selected_course = next((c for c in courses if c['course_id'] == sel_course_id), None)
        else:
            st.warning("등록된 커리큘럼이 없습니다.")

# ---------------------------------------------------------
# [A] 학습 모드 (Student)
# ---------------------------------------------------------
if mode == "👨‍🎓 학습 모드 (Student)":
    if selected_course:
        st.subheader(f"📘 {selected_course['title']}")
        chapters = selected_course.get('chapters', [])
        chapter_titles = [f"Chapter {ch['chapter_id']}. {ch['title']}" for ch in chapters]
        sel_ch_idx = st.selectbox("챕터 선택", range(len(chapters)), format_func=lambda i: chapter_titles[i])
        current_ch = chapters[sel_ch_idx]
        
        tab1, tab2, tab3 = st.tabs(["📖 이론", "🧪 시뮬레이터", "🔥 실전 기출"])
        
        with tab1:
            st.markdown(current_ch.get('theory_markdown', '내용 없음'))
            
        with tab2:
            sim_type = current_ch.get('simulator_type', 'default')
            defaults = current_ch.get('simulator_defaults', {})
            
            if "bond" in sim_type:
                c1, c2 = st.columns([1, 2])
                with c1:
                    face = st.number_input("액면", value=defaults.get('face', 100000), step=10000)
                    crate = st.number_input("표시이자(%)", value=defaults.get('crate', 0.05)*100)/100
                    mrate = st.number_input("유효이자(%)", value=defaults.get('mrate', 0.08)*100)/100
                    periods = st.slider("만기", 1, 10, 3)
                with c2:
                    p, df = Simulators.bond_basic(face, crate, mrate, periods)
                    st.metric("PV", f"{p:,}원")
                    st.dataframe(df, use_container_width=True)
            elif "depreciation" in sim_type:
                c1, c2 = st.columns([1, 2])
                with c1:
                    cost = st.number_input("취득원가", value=defaults.get('cost', 1000000))
                    resid = st.number_input("잔존가치", value=defaults.get('residual', 100000))
                    life = st.number_input("내용연수", value=defaults.get('life', 5))
                    rate = None
                    if "db" in sim_type: rate = st.number_input("상각률", value=defaults.get('rate', 0.451))
                    
                    m_code = "SL"
                    if "db" in sim_type: m_code = "DB"
                    elif "syd" in sim_type: m_code = "SYD"
                with c2:
                    df = Simulators.depreciation(cost, resid, life, m_code, rate)
                    st.line_chart(df["기말장부"].str.replace(",","").astype(int))
                    st.dataframe(df, use_container_width=True)
            elif "inventory" in sim_type:
                c1, c2 = st.columns(2)
                with c1:
                    bq, bp = st.number_input("기초수량", 100), st.number_input("기초단가", 100)
                with c2:
                    buyq, buyp = st.number_input("매입수량", 100), st.number_input("매입단가", 120)
                sq = st.slider("판매수량", 0, bq+buyq, 150)
                if "fifo" in sim_type:
                    cogs, end, r1, r2 = Simulators.inventory_fifo(bq, bp, buyq, buyp, sq)
                    st.success(f"매출원가: {cogs:,}원")
                    st.info(f"기말재고: {end:,}원")
            else:
                st.info("시각화가 필요 없는 이론 챕터입니다.")

        with tab3:
            kws = current_ch.get('related_keywords', [])
            if kws:
                all_qs = load_questions()
                matched = find_related_questions(kws, all_qs)
                if matched:
                    st.success(f"🔍 관련 문제 {len(matched)}개 발견")
                    q_opts = {q['question_id']: f"[{q.get('exam_info',{}).get('year','-')}] {q['topic']}" for q in matched}
                    qid = st.selectbox("문제 선택", list(q_opts.keys()), format_func=lambda x: q_opts[x])
                    q_data = next(q for q in matched if q['question_id'] == qid)
                    
                    st.divider()
                    c_q, c_a = st.columns([1.5, 1])
                    with c_q:
                        st.markdown(f"**Q. {q_data['topic']}**")
                        st.markdown(q_data['content_markdown'])
                        if q_data.get('choices'):
                            opts = q_data['choices']
                            if isinstance(opts, dict): opts = [f"{k}. {v}" for k,v in sorted(opts.items())]
                            st.radio("정답", opts, label_visibility="collapsed")
                    with c_a:
                        with st.expander("💡 해설 보기"):
                            st.info(f"정답: {q_data.get('answer', '?')}")
                            sols = q_data.get('solution_steps') or q_data.get('steps')
                            if sols:
                                for s in sols:
                                    st.markdown(f"**{s.get('title','Step')}**")
                                    st.caption(s.get('content',''))
                                    st.divider()
                            else:
                                st.warning("해설 없음")
                                if GEMINI_AVAILABLE and st.button("🤖 AI 해설 요청"):
                                    st.info("AI 기능 호출됨 (실제 구현 시 API 사용)")
                else:
                    st.info(f"'{kws}' 관련 문제가 없습니다.")
            else:
                st.info("키워드가 등록되지 않았습니다.")

# ---------------------------------------------------------
# [B] 관리자 모드 (Admin) - AgGrid 적용됨 ✨
# ---------------------------------------------------------
elif mode == "🛠️ 관리자 모드 (Admin)":
    st.header("🛠️ 통합 데이터 관리 센터 (with AgGrid)")
    
    tab_course, tab_quest, tab_clinic = st.tabs(["📚 커리큘럼", "📥 대량 등록", "🏥 해설 클리닉"])
    
    # 1. 커리큘럼 (JSON 등록 유지)
    with tab_course:
        st.caption("커리큘럼은 구조가 복잡하여 JSON 업로드 방식을 권장합니다.")
        c_json = st.text_area("Curriculum JSON", height=150)
        if st.button("커리큘럼 저장"):
            try:
                data = json.loads(c_json)
                if not isinstance(data, list): data = [data]
                save_json_batch("courses", data, "course_id")
                st.success("저장 완료")
                load_courses.clear()
            except Exception as e: st.error(e)
            
        # [NEW] 등록된 커리큘럼 현황 (Grid)
        courses = load_courses()
        if courses:
            df_c = pd.DataFrame(courses)
            # 필요한 컬럼만 보기 좋게 정리
            df_view = df_c[['course_id', 'engine_type', 'title']].copy()
            df_view['chapters'] = df_c['chapters'].apply(lambda x: len(x) if isinstance(x, list) else 0)
            
            st.markdown("#### 📊 등록된 코스 현황")
            AgGrid(df_view, fit_columns_on_grid_load=True, height=200)

    # 2. 대량 등록 (기존 유지)
    with tab_quest:
        st.info("문제/해설 JSON 대량 업로드")
        q_json = st.text_area("Data JSON", height=200)
        c1, c2 = st.columns(2)
        with c1:
            if st.button("문제 업로드"):
                try:
                    d = json.loads(q_json)
                    if not isinstance(d, list): d = [d]
                    save_json_batch("questions", d, "question_id")
                    st.success("완료")
                    load_questions.clear()
                except Exception as e: st.error(e)
        with c2:
            if st.button("해설 합체"):
                st.info("해설 업데이트 로직 동작")

    # 3. 해설 클리닉 (AgGrid의 진가 발휘!)
    with tab_clinic:
        st.markdown("#### 🏥 문제 조회 및 수정")
        st.caption("아래 표에서 문제를 선택(체크)하면 하단에 수정 에디터가 열립니다.")
        
        all_qs = load_questions()
        if all_qs:
            # 1) 그리드용 데이터프레임 만들기 (가볍게)
            df_q = pd.DataFrame(all_qs)
            
            # 컬럼 정리 (없으면 생성)
            if 'engine_type' not in df_q.columns: df_q['engine_type'] = '-'
            if 'exam_info' in df_q.columns:
                df_q['year'] = df_q['exam_info'].apply(lambda x: x.get('year','-') if isinstance(x, dict) else '-')
            else:
                df_q['year'] = '-'
                
            # 해설 유무 체크 (O/X)
            def check_sol(row):
                if row.get('solution_steps') or row.get('steps'): return "O"
                return "X"
            df_q['has_sol'] = df_q.apply(check_sol, axis=1)
            
            # 표시할 컬럼만 선택
            df_grid = df_q[['question_id', 'year', 'engine_type', 'topic', 'has_sol']].copy()
            
            # 2) AgGrid 설정
            gb = GridOptionsBuilder.from_dataframe(df_grid)
            gb.configure_pagination(paginationAutoPageSize=False, paginationPageSize=10) # 10개씩 보기
            gb.configure_selection('single', use_checkbox=True) # 체크박스 선택
            gb.configure_column("question_id", header_name="ID", width=120)
            gb.configure_column("topic", header_name="주제", width=300)
            gb.configure_column("has_sol", header_name="해설", width=80, cellStyle={'textAlign': 'center'})
            gridOptions = gb.build()
            
            # 3) 그리드 출력
            grid_response = AgGrid(
                df_grid, 
                gridOptions=gridOptions, 
                update_mode=GridUpdateMode.SELECTION_CHANGED, 
                fit_columns_on_grid_load=True,
                height=350, 
                theme='streamlit'
            )
            
            # 4) 선택된 행 처리 (오류 수정됨 ✨)
            selected = grid_response['selected_rows']

            # [핵심 수정] selected가 DataFrame일 경우 리스트로 변환
            if isinstance(selected, pd.DataFrame):
                selected = selected.to_dict('records')

            # 이제 selected는 무조건 리스트이므로 안전함
            if selected:
                sel_row = selected[0] 
                sel_id = sel_row['question_id']
                
                st.divider()
                st.markdown(f"### ✏️ 편집 모드: {sel_id}")
                
                # 원본 데이터 가져오기
                target_q = next((q for q in all_qs if q['question_id'] == sel_id), None)
                
                if target_q:
                    # 해설 데이터 추출
                    current_sols = target_q.get('solution_steps') or target_q.get('steps') or []
                    
                    # JSON 에디터
                    new_json = st.text_area(
                        "해설 데이터 (JSON)", 
                        value=json.dumps(current_sols, indent=2, ensure_ascii=False),
                        height=300
                    )
                    
                    c_save, c_del = st.columns([1, 4])
                    with c_save:
                        if st.button("💾 저장하기"):
                            try:
                                new_sols = json.loads(new_json)
                                db.collection("questions").document(sel_id).update({"solution_steps": new_sols})
                                st.success("수정 완료! 목록을 갱신합니다.")
                                load_questions.clear() # 캐시 삭제
                                st.rerun()
                            except Exception as e: st.error(f"JSON 오류: {e}")