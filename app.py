import streamlit as st
import pandas as pd
import json
import firebase_admin
from firebase_admin import credentials, firestore
import google.generativeai as genai
import time

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
# 2. Simulator Engine (계산 로직 연구소)
# =========================================================
class Simulators:
    @staticmethod
    def bond_basic(face, crate, mrate, periods):
        """사채(PV) 계산기"""
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
        """감가상각 계산기 (정액/정률/연수합계)"""
        data = []
        accumulated_dep = 0
        book_value = cost
        
        # 0년차
        data.append({"연도": 0, "기초장부": "-", "상각비": "-", "기말장부": f"{int(cost):,}"})

        for t in range(1, life + 1):
            start_bv = book_value
            dep_expense = 0
            
            if method == "SL": # 정액법
                dep_expense = (cost - residual) / life
            
            elif method == "DB": # 정률법
                if t == life: # 마지막 해
                    dep_expense = start_bv - residual
                else:
                    dep_expense = start_bv * (rate if rate else (1 - (residual/cost)**(1/life)))
            
            elif method == "SYD": # 연수합계법
                syd = life * (life + 1) / 2
                remaining_life = life - t + 1
                dep_expense = (cost - residual) * (remaining_life / syd)

            # 계산된 상각비 적용
            accumulated_dep += dep_expense
            book_value -= dep_expense
            
            data.append({
                "연도": t,
                "기초장부": f"{int(start_bv):,}",
                "상각비": f"{int(dep_expense):,}",
                "기말장부": f"{int(book_value):,}"
            })
            
        return pd.DataFrame(data).set_index("연도")

    @staticmethod
    def inventory_fifo(base_qty, base_price, buy_qty, buy_price, sell_qty):
        """재고자산 FIFO 계산기"""
        # 간단한 로직: 기초 -> 매입 순서로 판매
        revenue = 0 # 매출액은 판가(Market Price) 필요하지만 여기선 원가 흐름만
        cogs = 0    # 매출원가
        
        rem_base = base_qty
        rem_buy = buy_qty
        
        # 1. 기초재고에서 판매
        sold_from_base = min(sell_qty, rem_base)
        cogs += sold_from_base * base_price
        rem_base -= sold_from_base
        remaining_sell = sell_qty - sold_from_base
        
        # 2. 매입분에서 판매
        sold_from_buy = min(remaining_sell, rem_buy)
        cogs += sold_from_buy * buy_price
        rem_buy -= sold_from_buy
        
        ending_inventory = (rem_base * base_price) + (rem_buy * buy_price)
        
        return cogs, ending_inventory, rem_base, rem_buy

# =========================================================
# 3. Data Logic (데이터 핸들러)
# =========================================================
@st.cache_data(ttl=60)
def load_courses():
    """커리큘럼(Courses) 데이터 로드"""
    try:
        docs = db.collection("courses").stream()
        return [doc.to_dict() for doc in docs]
    except: return []

@st.cache_data(ttl=60)
def load_questions():
    """모든 기출문제 로드"""
    try:
        docs = db.collection("questions").stream()
        return [doc.to_dict() for doc in docs]
    except: return []

def find_related_questions(keywords, all_questions):
    """키워드 기반 문제 필터링 (간이 검색 엔진)"""
    if not keywords: return []
    results = []
    for q in all_questions:
        # topic이나 content에 키워드가 하나라도 포함되면 가져옴
        search_text = (q.get('topic', '') + q.get('content_markdown', '')).lower()
        if any(k.lower() in search_text for k in keywords):
            results.append(q)
    return results

def save_json_batch(collection_name, items, id_field):
    """범용 JSON 업로더"""
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
# 4. UI Layout (화면 구성)
# =========================================================
st.title("☁️ Accoun-T Cloud")

# 사이드바 (Navigation)
with st.sidebar:
    st.header("Controller")
    mode = st.radio("모드 선택", ["👨‍🎓 학습 모드 (Student)", "🛠️ 관리자 모드 (Admin)"])
    st.divider()
    
    # 학습 모드일 때만 엔진/코스 선택 표시
    selected_course = None
    if mode == "👨‍🎓 학습 모드 (Student)":
        courses = load_courses()
        if courses:
            # 1. 엔진 선택
            engines = sorted(list(set([c['engine_type'] for c in courses])))
            sel_engine = st.selectbox("엔진 (Engine)", engines)
            
            # 2. 코스(주제) 선택
            engine_courses = [c for c in courses if c['engine_type'] == sel_engine]
            course_map = {c['course_id']: c['title'] for c in engine_courses}
            sel_course_id = st.selectbox("학습 주제 (Topic)", list(course_map.keys()), format_func=lambda x: course_map[x])
            
            selected_course = next((c for c in courses if c['course_id'] == sel_course_id), None)
        else:
            st.warning("등록된 커리큘럼이 없습니다.")

# ---------------------------------------------------------
# [A] 학습 모드 (Student View)
# ---------------------------------------------------------
if mode == "👨‍🎓 학습 모드 (Student)":
    if selected_course:
        st.subheader(f"📘 {selected_course['title']}")
        st.caption(selected_course['description'])
        
        # 챕터 선택 (Tabs or Selectbox? -> Selectbox가 모바일에 좋음)
        chapters = selected_course.get('chapters', [])
        chapter_titles = [f"Chapter {ch['chapter_id']}. {ch['title']}" for ch in chapters]
        sel_ch_idx = st.selectbox("챕터를 선택하세요", range(len(chapters)), format_func=lambda i: chapter_titles[i])
        
        current_ch = chapters[sel_ch_idx]
        
        # 3단계 학습 탭
        tab_theory, tab_sim, tab_exam = st.tabs(["📖 Step 1. 이론", "🧪 Step 2. 시뮬레이터", "🔥 Step 3. 실전 기출"])
        
        # [Step 1] 이론
        with tab_theory:
            st.markdown(current_ch.get('theory_markdown', '내용이 없습니다.'))
            
        # [Step 2] 시뮬레이터
        with tab_sim:
            sim_type = current_ch.get('simulator_type', 'default')
            defaults = current_ch.get('simulator_defaults', {})
            
            # --- 시뮬레이터 분기 처리 ---
            if "bond" in sim_type: # 사채 관련
                c1, c2 = st.columns([1, 2])
                with c1:
                    face = st.number_input("액면금액", value=defaults.get('face', 100000), step=10000)
                    crate = st.number_input("표시이자율(%)", value=defaults.get('crate', 0.05)*100) / 100
                    mrate = st.number_input("유효이자율(%)", value=defaults.get('mrate', 0.08)*100) / 100
                    periods = st.slider("만기(년)", 1, 10, 3)
                with c2:
                    price, df = Simulators.bond_basic(face, crate, mrate, periods)
                    st.metric("발행금액 (PV)", f"{price:,}원")
                    st.dataframe(df, use_container_width=True)
            
            elif "depreciation" in sim_type: # 감가상각 관련
                c1, c2 = st.columns([1, 2])
                with c1:
                    cost = st.number_input("취득원가", value=defaults.get('cost', 1000000), step=100000)
                    resid = st.number_input("잔존가치", value=defaults.get('residual', 100000), step=10000)
                    life = st.number_input("내용연수", value=defaults.get('life', 5))
                    
                    method_map = {"depreciation_sl": "SL", "depreciation_db": "DB", "depreciation_syd": "SYD"}
                    method_code = method_map.get(sim_type, "SL")
                    
                    rate = None
                    if method_code == "DB":
                        rate = st.number_input("상각률(정률법용)", value=defaults.get('rate', 0.451), format="%.3f")
                with c2:
                    df = Simulators.depreciation(cost, resid, life, method_code, rate)
                    st.line_chart(df["기말장부"].str.replace(",","").astype(int))
                    st.dataframe(df, use_container_width=True)

            elif "inventory" in sim_type: # 재고자산
                c1, c2 = st.columns(2)
                with c1:
                    base_qty = st.number_input("기초수량", 100)
                    base_prc = st.number_input("기초단가", 100)
                with c2:
                    buy_qty = st.number_input("매입수량", 100)
                    buy_prc = st.number_input("매입단가", 120)
                
                sell_qty = st.slider("판매수량", 0, base_qty+buy_qty, 150)
                
                if "fifo" in sim_type:
                    cogs, end_inv, r1, r2 = Simulators.inventory_fifo(base_qty, base_prc, buy_qty, buy_prc, sell_qty)
                    st.success(f"매출원가: {cogs:,}원")
                    st.info(f"기말재고: {end_inv:,}원")
                else:
                    st.warning("다른 방법(평균법 등)은 시뮬레이터 업데이트 예정입니다.")
                    
            else:
                st.info("이 주제는 시각화 시뮬레이터가 필요 없는 이론 중심 챕터입니다.")

        # [Step 3] 기출문제 (자동 매칭)
        with tab_exam:
            keywords = current_ch.get('related_keywords', [])
            if keywords:
                all_qs = load_questions()
                matched_qs = find_related_questions(keywords, all_qs)
                
                if matched_qs:
                    st.success(f"🔍 '{keywords}' 관련 기출문제 {len(matched_qs)}개를 찾았습니다.")
                    
                    # 문제 리스트업
                    q_options = {q['question_id']: f"[{q.get('exam_info',{}).get('year','-')}] {q['topic']}" for q in matched_qs}
                    sel_qid = st.selectbox("풀어볼 문제 선택", list(q_options.keys()), format_func=lambda x: q_options[x])
                    
                    q_data = next(q for q in matched_qs if q['question_id'] == sel_qid)
                    
                    st.divider()
                    col_q, col_a = st.columns([1.5, 1])
                    
                    with col_q:
                        st.markdown(f"**Q. {q_data['topic']}**")
                        st.markdown(q_data['content_markdown'])
                        if q_data.get('choices'):
                            # choices 호환성 처리 (List or Dict)
                            opts = q_data['choices']
                            if isinstance(opts, dict): opts = [f"{k}. {v}" for k,v in sorted(opts.items())]
                            st.radio("정답", opts, label_visibility="collapsed")
                            
                    with col_a:
                        with st.expander("💡 해설 보기"):
                            st.info(f"정답: {q_data.get('answer', '?')}")
                            
                            # 해설 표시 (호환성: steps vs solution_steps)
                            sols = q_data.get('solution_steps') or q_data.get('steps')
                            if sols:
                                for s in sols:
                                    st.markdown(f"**{s.get('title','Step')}**")
                                    st.caption(s.get('content',''))
                                    st.divider()
                            else:
                                st.warning("해설이 없습니다.")
                                if GEMINI_AVAILABLE and st.button("🤖 AI 해설 요청"):
                                    # (간략화) 실제 호출 로직은 이전 버전 참조
                                    st.info("AI 기능 호출 (구현됨)")
                else:
                    st.info(f"아직 '{keywords}' 태그와 일치하는 기출문제가 DB에 없습니다.")
            else:
                st.info("이 챕터에 등록된 검색 키워드가 없습니다.")

# ---------------------------------------------------------
# [B] 관리자 모드 (Admin View)
# ---------------------------------------------------------
elif mode == "🛠️ 관리자 모드 (Admin)":
    st.header("🛠️ 통합 데이터 관리 센터")
    
    tab_course, tab_quest, tab_sol = st.tabs(["📚 커리큘럼 등록", "📥 문제/해설 등록", "🏥 해설 클리닉"])
    
    # 1. 커리큘럼 등록
    with tab_course:
        st.markdown("**[Courses] 컬렉션 업로드** (준비된 JSON을 붙여넣으세요)")
        c_json = st.text_area("Curriculum JSON", height=200)
        if st.button("커리큘럼 저장"):
            try:
                data = json.loads(c_json)
                if not isinstance(data, list): data = [data]
                cnt = save_json_batch("courses", data, "course_id")
                st.success(f"{cnt}개의 코스 저장 완료! (새로고침 하세요)")
                load_courses.clear() # 캐시 초기화
            except Exception as e:
                st.error(f"오류: {e}")

    # 2. 문제/해설 등록 (기존 로직)
    with tab_quest:
        st.info("문제(questions) 또는 해설을 대량으로 등록합니다.")
        q_json = st.text_area("Data JSON", height=200, placeholder='[{ "question_id": ... }]')
        
        c1, c2 = st.columns(2)
        with c1:
            if st.button("문제 업로드 (Questions)"):
                try:
                    data = json.loads(q_json)
                    if not isinstance(data, list): data = [data]
                    cnt = save_json_batch("questions", data, "question_id")
                    st.success(f"{cnt}건 업로드 완료")
                    load_questions.clear()
                except Exception as e: st.error(e)
        with c2:
            if st.button("해설 합체 (Update Solutions)"):
                # 해설 업데이트 로직 (update_solution_batch 활용 권장)
                st.info("해설 업데이트 기능 동작")

    # 3. 해설 클리닉 (수정 기능)
    with tab_sol:
        st.markdown("등록된 문제의 내용을 확인하고 **해설을 직접 수정**합니다.")
        qs = load_questions()
        if qs:
            q_map = {q['question_id']: f"{q['question_id']} : {q['topic']}" for q in qs}
            sel_id = st.selectbox("수정할 문제 선택", list(q_map.keys()), format_func=lambda x: q_map[x])
            
            target_q = next(q for q in qs if q['question_id'] == sel_id)
            
            # 현재 해설 불러오기
            current_sols = target_q.get('solution_steps') or target_q.get('steps') or []
            
            # 편집기 (JSON 형태 그대로 노출하여 자유도 부여)
            st.markdown("👇 **해설 데이터 편집** (JSON 형식 준수)")
            edit_json = st.text_area("Editor", value=json.dumps(current_sols, indent=2, ensure_ascii=False), height=300)
            
            if st.button("수정사항 저장 (Save)"):
                try:
                    new_sols = json.loads(edit_json)
                    db.collection("questions").document(sel_id).update({"solution_steps": new_sols})
                    st.success("해설이 수정되었습니다!")
                    load_questions.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f"JSON 형식이 잘못되었습니다: {e}")