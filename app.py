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
# 3. Data Logic (Enhanced Filter)
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

def advanced_filter_questions(all_qs, filters):
    """
    고급 필터링 로직 (Type Safe Version)
    """
    filtered = []
    
    for q in all_qs:
        # 1. 키워드 매칭
        if filters.get('keywords'):
            search_text = (q.get('topic', '') + q.get('content_markdown', '')).lower()
            tags = q.get('tags', [])
            if isinstance(tags, list): search_text += " ".join(tags).lower()
            
            if not any(k.lower() in search_text for k in filters['keywords']):
                continue

        # 2. 연도 필터 (Year Range) - 안전한 정수 변환
        try:
            q_year = int(q.get('exam_info', {}).get('year', 0))
        except (ValueError, TypeError):
            q_year = 0
            
        if filters.get('years'):
            min_y, max_y = filters['years']
            if q_year != 0 and not (min_y <= q_year <= max_y):
                continue
                
        # 3. 시험 유형 필터 (Exam Type)
        q_exam = q.get('exam_info', {}).get('type', '기타')
        if filters.get('exams'):
            if q_exam not in filters['exams']:
                continue
                
        # 4. 난이도 필터 - [🚨 핵심 수정 부분]
        # 데이터가 문자열("3")이어도 숫자로 강제 변환, 에러나면 0 처리
        try:
            q_diff = int(q.get('difficulty', 0))
        except (ValueError, TypeError):
            q_diff = 0
            
        if filters.get('difficulty'):
            min_d, max_d = filters['difficulty']
            # 난이도 정보가 없거나(0), 범위 밖이면 제외
            if q_diff != 0 and not (min_d <= q_diff <= max_d):
                continue

        filtered.append(q)
            
    return filtered

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

def delete_document(collection_name, doc_id):
    db.collection(collection_name).document(str(doc_id)).delete()

# =========================================================
# 4. UI Layout
# =========================================================
st.title("☁️ Accoun-T Cloud")

# 전역 데이터 로드 (필터링 위젯 구성을 위해 미리 로드)
all_questions_raw = load_questions()
all_courses = load_courses()

# --- 사이드바 (Controller) ---
with st.sidebar:
    st.header("Controller")
    mode = st.radio("모드 선택", ["👨‍🎓 학습 모드 (Student)", "🛠️ 관리자 모드 (Admin)"])
    st.divider()
    
    # [학습 모드 전용] 필터링 UI
    student_filters = {}
    selected_course = None
    
    if mode == "👨‍🎓 학습 모드 (Student)":
        # 1. 커리큘럼 선택
        if all_courses:
            engines = sorted(list(set([c['engine_type'] for c in all_courses])))
            sel_engine = st.selectbox("엔진 (Engine)", engines)
            engine_courses = [c for c in all_courses if c['engine_type'] == sel_engine]
            course_map = {c['course_id']: c['title'] for c in engine_courses}
            sel_course_id = st.selectbox("학습 주제 (Topic)", list(course_map.keys()), format_func=lambda x: course_map[x])
            selected_course = next((c for c in all_courses if c['course_id'] == sel_course_id), None)
        
        st.divider()
        st.markdown("### 🔍 맞춤 문제 필터")
        
        # 필터 1: 시험 유형 (Dynamic)
        all_exams = set()
        for q in all_questions_raw:
            e_type = q.get('exam_info', {}).get('type')
            if e_type: all_exams.add(e_type)
        if not all_exams: all_exams = {"기타"}
        
        sel_exams = st.multiselect("시험 유형", sorted(list(all_exams)), default=[])
        
        # 필터 2: 연도 범위 (Dynamic)
        all_years = []
        for q in all_questions_raw:
            try: y = int(q.get('exam_info', {}).get('year', 0))
            except: y = 0
            if y > 2000: all_years.append(y)
            
        min_year, max_year = 2010, 2025 # 기본값
        
        if all_years:
            min_year, max_year = min(all_years), max(all_years)
            
        # [🚨 핵심 수정] 최소값과 최대값이 같으면 슬라이더가 에러를 뿜습니다.
        # 데이터가 1개 연도만 있을 경우, 앞뒤로 1년씩 강제로 범위를 늘려줍니다.
        if min_year == max_year:
            min_year -= 1
            max_year += 1
            
        sel_years = st.slider("연도 범위", min_year, max_year, (min_year, max_year))
        
        # 필터 3: 난이도
        sel_diff = st.slider("난이도 (1~5)", 1, 5, (1, 5))
        
        # 필터 저장
        student_filters = {
            'exams': sel_exams,
            'years': sel_years,
            'difficulty': sel_diff,
            'keywords': [] # 챕터별 키워드는 메인 화면에서 주입
        }

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
            # Markdown Quote/Bold issue safe render
            content = current_ch.get('theory_markdown', '내용 없음')
            st.markdown(content)
            
        with tab2:
            # (시뮬레이터 코드는 기존과 동일하여 생략 없이 유지)
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
                st.info("이론 중심 챕터입니다.")

        with tab3:
            # 1. 챕터 키워드 가져오기
            chapter_keywords = current_ch.get('related_keywords', [])
            
            if chapter_keywords:
                # 2. 필터 병합 (챕터 키워드 + 사이드바 필터)
                student_filters['keywords'] = chapter_keywords
                
                # 3. 필터링 실행
                matched_qs = advanced_filter_questions(all_questions_raw, student_filters)
                
                if matched_qs:
                    st.success(f"🔍 조건에 맞는 문제 {len(matched_qs)}개를 찾았습니다.")
                    
                    # 4. 문제 선택 UI
                    q_opts = {}
                    for q in matched_qs:
                        year = q.get('exam_info', {}).get('year', '-')
                        etype = q.get('exam_info', {}).get('type', '')
                        q_opts[q['question_id']] = f"[{year} {etype}] {q['topic']}"
                        
                    qid = st.selectbox("문제 선택", list(q_opts.keys()), format_func=lambda x: q_opts[x])
                    q_data = next(q for q in matched_qs if q['question_id'] == qid)
                    
                    st.divider()
                    
                    # 5. 메타데이터 뱃지 표시
                    tags = q_data.get('tags', [])
                    if tags:
                        st.caption("Tags: " + " ".join([f"`#{t}`" for t in tags]))
                    
                    # 6. 문제 표시
                    c_q, c_a = st.columns([1.5, 1])
                    with c_q:
                        st.markdown(f"**Q. {q_data['topic']}**")
                        st.markdown(q_data['content_markdown'])
                        
                        opts = q_data.get('choices')
                        if opts:
                            if isinstance(opts, dict): opts_list = [f"{k}. {v}" for k,v in sorted(opts.items())]
                            else: opts_list = opts
                            st.radio("정답", opts_list, label_visibility="collapsed")
                            
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
                                    st.info("AI 기능 호출됨")
                else:
                    st.warning("조건에 맞는 문제가 없습니다. 사이드바 필터를 조정해보세요.")
            else:
                st.info("이 챕터에는 연결된 태그(Keywords)가 없습니다.")

# ---------------------------------------------------------
# [B] 관리자 모드 (Admin) - Enhanced Grid
# ---------------------------------------------------------
elif mode == "🛠️ 관리자 모드 (Admin)":
    st.header("🛠️ 통합 관리 센터")
    tab_course, tab_quest = st.tabs(["📚 커리큘럼 관리", "📥 문제/해설 통합 관리"])
    
    # 1. 커리큘럼 (기존 유지)
    with tab_course:
        st.markdown("#### 1️⃣ 등록된 코스 목록")
        if all_courses:
            df_c = pd.DataFrame(all_courses)
            df_view = df_c[['course_id', 'engine_type', 'title']].copy()
            df_view['chapters_count'] = df_c['chapters'].apply(lambda x: len(x) if isinstance(x, list) else 0)
            
            gb = GridOptionsBuilder.from_dataframe(df_view)
            gb.configure_selection('single', use_checkbox=True)
            gb.configure_column("course_id", width=100)
            gb.configure_column("title", width=300)
            grid_resp = AgGrid(df_view, gridOptions=gb.build(), update_mode=GridUpdateMode.SELECTION_CHANGED, fit_columns_on_grid_load=True, height=200)
            
            selected = grid_resp['selected_rows']
            if isinstance(selected, pd.DataFrame): selected = selected.to_dict('records')
        else: selected = []

        st.divider()
        edit_target = {}
        header_text = "🆕 신규 커리큘럼 등록"
        if selected:
            edit_target = next(c for c in all_courses if c['course_id'] == selected[0]['course_id'])
            header_text = f"✏️ 수정 모드: {edit_target['course_id']}"
            
        st.subheader(header_text)
        default_val = json.dumps(edit_target, indent=2, ensure_ascii=False) if edit_target else ""
        c_json = st.text_area("Course JSON", value=default_val, height=300)
        
        c1, c2 = st.columns([1, 5])
        with c1:
            if st.button("💾 저장"):
                try:
                    data = json.loads(c_json)
                    if not isinstance(data, list): data = [data]
                    save_json_batch("courses", data, "course_id")
                    st.success("저장 완료")
                    load_courses.clear()
                    st.rerun()
                except Exception as e: st.error(e)
        with c2:
            if selected and st.button("🗑️ 삭제"):
                delete_document("courses", selected[0]['course_id'])
                st.success("삭제 완료")
                load_courses.clear()
                st.rerun()

    # 2. 문제/해설 통합 (메타데이터 컬럼 추가 & 방어 로직 적용)
    with tab_quest:
        st.markdown("#### 2️⃣ 등록된 문제 목록 (필터링 강화)")
        
        if all_questions_raw:
            df_q = pd.DataFrame(all_questions_raw)
            
            # --- [🚨 핵심 수정] 데이터가 없을 경우를 대비한 방어 로직 ---
            # 컬럼이 아예 없으면 생성해줍니다.
            if 'exam_info' not in df_q.columns:
                df_q['exam_info'] = None
            if 'tags' not in df_q.columns:
                df_q['tags'] = None
            if 'engine_type' not in df_q.columns: 
                df_q['engine_type'] = '-'
            if 'topic' not in df_q.columns:
                df_q['topic'] = '제목 없음'
            # -------------------------------------------------------
            
            # --- Grid용 데이터 가공 (Flattening) ---
            # 1. Exam Info 분리 (안전하게 처리)
            df_q['year'] = df_q['exam_info'].apply(lambda x: x.get('year', 0) if isinstance(x, dict) else 0)
            df_q['exam'] = df_q['exam_info'].apply(lambda x: x.get('type', '-') if isinstance(x, dict) else '-')
            
            # 2. Tags를 문자열로 변환 (리스트가 아니거나 None이면 빈 문자열)
            df_q['tags_str'] = df_q['tags'].apply(lambda x: ", ".join(x) if isinstance(x, list) else "")
            
            # 3. 해설 유무
            df_q['has_sol'] = df_q.apply(lambda r: "O" if (r.get('solution_steps') or r.get('steps')) else "X", axis=1)
            
            # 필요한 컬럼만 선택
            df_grid = df_q[['question_id', 'year', 'exam', 'engine_type', 'topic', 'tags_str', 'has_sol']].copy()
            
            # AgGrid 설정
            gb_q = GridOptionsBuilder.from_dataframe(df_grid)
            gb_q.configure_selection('single', use_checkbox=True)
            gb_q.configure_pagination(paginationAutoPageSize=False, paginationPageSize=10)
            
            # 컬럼 디테일 설정
            gb_q.configure_column("question_id", header_name="ID", width=100, pinned=True)
            gb_q.configure_column("year", header_name="연도", width=80)
            gb_q.configure_column("exam", header_name="시험", width=80)
            gb_q.configure_column("topic", header_name="주제", width=250)
            gb_q.configure_column("tags_str", header_name="태그", width=150)
            gb_q.configure_column("has_sol", header_name="해설", width=70, cellStyle={'textAlign': 'center'})
            
            gridOpts_q = gb_q.build()
            grid_resp_q = AgGrid(df_grid, gridOptions=gridOpts_q, update_mode=GridUpdateMode.SELECTION_CHANGED, fit_columns_on_grid_load=True, height=350)
            
            sel_q = grid_resp_q['selected_rows']
            if isinstance(sel_q, pd.DataFrame): sel_q = sel_q.to_dict('records')
        else:
            st.info("등록된 문제가 없습니다.")
            sel_q = []
            
        st.divider()
        
        target_q_data = {}
        header_text_q = "🆕 신규 문제/해설 등록"
        if sel_q:
            sel_id = sel_q[0]['question_id']
            # 원본 데이터에서 찾을 때 안전하게
            target_q_data = next((q for q in all_questions_raw if q['question_id'] == sel_id), {})
            header_text_q = f"✏️ 수정 모드: {sel_id}"
            
        st.subheader(header_text_q)
        default_val_q = json.dumps(target_q_data, indent=2, ensure_ascii=False) if target_q_data else ""
        q_json = st.text_area("Question JSON", value=default_val_q, height=400)
        
        qc1, qc2 = st.columns([1, 5])
        with qc1:
            if st.button("💾 문제 저장"):
                try:
                    data = json.loads(q_json)
                    if not isinstance(data, list): data = [data]
                    cnt = save_json_batch("questions", data, "question_id")
                    st.success(f"{cnt}건 저장 완료")
                    load_questions.clear()
                    st.rerun()
                except Exception as e: st.error(e)
        with qc2:
            if sel_q and st.button("🗑️ 문제 삭제"):
                delete_document("questions", sel_q[0]['question_id'])
                st.success("삭제 완료")
                load_questions.clear()
                st.rerun()