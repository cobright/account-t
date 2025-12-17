import streamlit as st
import pandas as pd
import json
import firebase_admin
from firebase_admin import credentials, firestore
import google.generativeai as genai
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, DataReturnMode
import uuid  # 블록 ID 생성을 위해 추가

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
    def bond_basic(face, crate, mrate, periods, redeem_stats=None):
        """
        redeem_stats = {'period': 2, 'amount': 98000} (선택사항)
        """
        cash_flow = face * crate
        pv_principal = face / ((1 + mrate) ** periods)
        pv_interest = sum([cash_flow / ((1 + mrate) ** t) for t in range(1, periods + 1)])
        price = pv_principal + pv_interest
        
        data = []
        book_value = price
        data.append({"기간": 0, "유효이자": "-", "표시이자": "-", "상각액": "-", "장부금액": f"{int(book_value):,}"})
        
        # 상각표 작성
        bv_dict = {0: book_value} # 기간별 장부금액 저장
        
        for t in range(1, periods + 1):
            ie = book_value * mrate
            cp = face * crate
            am = ie - cp
            book_value += am
            bv_dict[t] = book_value
            data.append({
                "기간": t,
                "유효이자": f"{int(ie):,}", "표시이자": f"{int(cp):,}",
                "상각액": f"{int(am):,}", "장부금액": f"{int(book_value):,}"
            })
            
        # [Insight 생성]
        diff_type = "할인" if mrate > crate else ("할증" if mrate < crate else "액면")
        
        # (A) 기본 리포트
        insight = f"""
        **📊 분석 리포트**
        1. **발행 형태**: 시장이자율({mrate*100}%)이 표시이자율({crate*100}%)보다 {('높아' if mrate > crate else '낮아')} **{diff_type}발행**되었습니다.
        2. **장부금액 추세**: 만기({periods}년)로 갈수록 장부금액이 **{int(price):,}원**에서 **{int(face):,}원**을 향해 {('증가' if diff_type=='할인' else '감소')}합니다.
        """

        # (B) 조기상환 리포트 (추가된 부분 ✨)
        if redeem_stats:
            r_period = redeem_stats['period']
            r_amt = redeem_stats['amount']
            r_bv = bv_dict.get(r_period, 0)
            
            gain_loss = r_bv - r_amt
            gl_text = "상환이익(Gain)" if gain_loss >= 0 else "상환손실(Loss)"
            
            insight += f"""
            ---
            **💰 조기상환 손익 분석 ({r_period}년 말 상환 가정)**
            1. **장부상 빚**: {r_period}년 말 시점의 장부금액은 **{int(r_bv):,}원**입니다.
            2. **실제 갚은 돈**: **{int(r_amt):,}원**을 지급하고 빚을 청산했습니다.
            3. **결론**: 장부보다 {('적게' if gain_loss > 0 else '많이')} 주었으므로, **{abs(int(gain_loss)):,}원의 {gl_text}**이 발생합니다.
            """
            
        return int(price), pd.DataFrame(data).set_index("기간"), insight

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
            
        # [Insight 생성]
        method_map = {"SL": "정액법", "DB": "정률법", "SYD": "연수합계법"}
        trend = "매년 일정합니다" if method == "SL" else "초기에 크고 점차 감소합니다 (가속상각)"
        insight = f"""
        **📊 분석 리포트**
        1. **상각 방법**: **{method_map.get(method, method)}**을 적용했습니다.
        2. **비용 추세**: 감가상각비가 **{trend}**.
        3. **최종 잔액**: {life}년 후 장부금액(**{int(book_value):,}원**)은 잔존가치(**{int(residual):,}원**)와 정확히 일치합니다.
        """
        return pd.DataFrame(data).set_index("연도"), insight

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
        
        # [Insight 생성]
        price_trend = "상승" if buy_price > base_price else "하락"
        profit_effect = "과대계상(이익 ↑)" if price_trend == "상승" else "과소계상(이익 ↓)"
        insight = f"""
        **📊 분석 리포트 (FIFO 가정)**
        1. **물가 추세**: 단가가 {base_price}원에서 {buy_price}원으로 **{price_trend}**했습니다.
        2. **손익 효과**: 선입선출법은 옛날 싼 재고를 먼저 비용(원가) 처리하므로, 현재 시점에는 이익이 **{profit_effect}**되는 경향이 있습니다.
        3. **재고 상태**: 기말재고({int(ending):,}원)는 가장 **최근에 구입한 단가**로 구성되어 현행가치에 가깝습니다.
        """
        return cogs, ending, rem_base, rem_buy, insight

    @staticmethod
    def entity_equity(cost, share_rate, net_income, dividends):
        equity_income = net_income * share_rate
        div_received = dividends * share_rate
        ending_bv = cost + equity_income - div_received
        
        data = [
            {"구분": "1. 기초 취득원가", "금액": cost, "효과": "자산(+)"},
            {"구분": "2. 지분법이익(NI)", "금액": equity_income, "효과": "자산 증가(↑)"},
            {"구분": "3. 배당금수령(Div)", "금액": div_received, "효과": "자산 감소(↓)"},
            {"구분": "4. 기말 장부금액", "금액": ending_bv, "효과": "최종 잔액"}
        ]
        
        # [Insight 생성]
        insight = f"""
        **📊 분석 리포트**
        1. **성장의 공유**: 피투자회사가 번 돈({int(net_income):,}) 중 내 몫(**{int(equity_income):,}**)만큼 내 자산도 늘어났습니다.
        2. **배당의 의미**: 배당금(**{int(div_received):,}**)은 수익이 아니라, 투자했던 돈을 일부 **회수(자산 감소)**한 것으로 처리됩니다.
        3. **최종 결과**: 기초보다 장부금액이 **{int(ending_bv - cost):,}원** 변동했습니다.
        """
        return int(ending_bv), pd.DataFrame(data), insight
    

# =========================================================
# 3. Data Logic & Dan-gwon-hwa (Note Manager) ✨
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
    filtered = []
    for q in all_qs:
        if filters.get('keywords'):
            search_text = (q.get('topic', '') + q.get('content_markdown', '')).lower()
            tags = q.get('tags', [])
            if isinstance(tags, list): search_text += " ".join(tags).lower()
            if not any(k.lower() in search_text for k in filters['keywords']): continue
        try: q_year = int(q.get('exam_info', {}).get('year', 0))
        except: q_year = 0
        if filters.get('years'):
            min_y, max_y = filters['years']
            if q_year != 0 and not (min_y <= q_year <= max_y): continue
        q_exam = q.get('exam_info', {}).get('type', '기타')
        if filters.get('exams') and q_exam not in filters['exams']: continue
        try: q_diff = int(q.get('difficulty', 0))
        except: q_diff = 0
        if filters.get('difficulty'):
            min_d, max_d = filters['difficulty']
            if q_diff != 0 and not (min_d <= q_diff <= max_d): continue
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

def update_question_solution(question_id, solution_steps):
    """특정 문제의 해설 필드만 업데이트"""
    try:
        db.collection("questions").document(str(question_id)).update({
            "solution_steps": solution_steps
        })
        return True
    except Exception as e:
        st.error(f"데이터베이스 저장 실패: {e}")
        return False

def delete_document(collection_name, doc_id):
    db.collection(collection_name).document(str(doc_id)).delete()

def get_exam_questions(all_q, exam_type, exam_year):
    """특정 시험(예: 2024 CPA)의 문제들을 번호순으로 가져오기"""
    filtered = [
        q for q in all_q 
        if q.get('exam_info', {}).get('type') == exam_type 
        and q.get('exam_info', {}).get('year') == exam_year
    ]
    # question_id 기준으로 정렬 (예: 2024_CPA_01 -> 02 -> 03 ...)
    return sorted(filtered, key=lambda x: x.get('question_id', ''))

# [NEW] 단권화 관리 클래스
class NoteManager:
    @staticmethod
    def get_doc_id(user_id, course_id, chapter_id):
        # 문서 ID: "student_kim_ALLOC_001_1"
        return f"{user_id}_{course_id}_{chapter_id}"

    @staticmethod
    def parse_markdown_to_blocks(text):
        """기존 통짜 마크다운을 ## 제목 기준으로 잘라서 블록 리스트로 변환"""
        if not text: return []
        lines = text.split('\n')
        blocks = []
        current_content = []
        
        for line in lines:
            if line.strip().startswith("## "):
                # 이전 내용 저장
                if current_content:
                    blocks.append({
                        "id": str(uuid.uuid4())[:8],
                        "content": "\n".join(current_content),
                        "type": "system"
                    })
                current_content = [line]
            else:
                current_content.append(line)
        
        # 마지막 블록 저장
        if current_content:
            blocks.append({
                "id": str(uuid.uuid4())[:8],
                "content": "\n".join(current_content),
                "type": "system"
            })
        return blocks

    @staticmethod
    def load_user_notes(user_id, course_id, chapter_id, default_text):
        """DB에서 유저 노트를 불러오거나, 없으면 시스템 기본 텍스트를 블록화해서 리턴"""
        doc_id = NoteManager.get_doc_id(user_id, course_id, chapter_id)
        doc_ref = db.collection("user_notes").document(doc_id)
        doc = doc_ref.get()
        
        if doc.exists:
            # 유저가 저장한 단권화 데이터가 있으면 그걸 씀
            return doc.to_dict().get("blocks", [])
        else:
            # 없으면 시스템 기본 텍스트를 최초 1회 블록화
            return NoteManager.parse_markdown_to_blocks(default_text)

    @staticmethod
    def save_user_notes(user_id, course_id, chapter_id, blocks):
        doc_id = NoteManager.get_doc_id(user_id, course_id, chapter_id)
        db.collection("user_notes").document(doc_id).set({
            "user_id": user_id,
            "course_id": course_id,
            "chapter_id": chapter_id,
            "blocks": blocks,
            "updated_at": firestore.SERVER_TIMESTAMP
        })

# =========================================================
# 4. UI Layout
# =========================================================
st.title("☁️ Accoun-T Cloud")

# 가상의 사용자 ID (실제 로그인 기능 전까지 고정)
USER_ID = "student_demo"

all_questions_raw = load_questions()
all_courses = load_courses()

with st.sidebar:
    st.header("Controller")
    mode = st.radio("모드 선택", ["👨‍🎓 학습 모드 (Student)", "🛠️ 관리자 모드 (Admin)"])
    st.divider()
    
    student_filters = {}
    selected_course = None
    
    if mode == "👨‍🎓 학습 모드 (Student)":
        if all_courses:
            engines = sorted(list(set([c['engine_type'] for c in all_courses])))
            sel_engine = st.selectbox("엔진 (Engine)", engines)
            engine_courses = [c for c in all_courses if c['engine_type'] == sel_engine]
            course_map = {c['course_id']: c['title'] for c in engine_courses}
            sel_course_id = st.selectbox("학습 주제 (Topic)", list(course_map.keys()), format_func=lambda x: course_map[x])
            selected_course = next((c for c in all_courses if c['course_id'] == sel_course_id), None)
        
        st.divider()
        st.markdown("### 🔍 맞춤 문제 필터")
        # (필터 UI 생략 - 이전과 동일)
        all_exams = set()
        for q in all_questions_raw:
            e = q.get('exam_info', {}).get('type'); 
            if e: all_exams.add(e)
        if not all_exams: all_exams = {"기타"}
        sel_exams = st.multiselect("시험 유형", sorted(list(all_exams)), default=[])
        
        all_years = []
        for q in all_questions_raw:
            try: y = int(q.get('exam_info', {}).get('year', 0))
            except: y = 0
            if y > 2000: all_years.append(y)
        min_y, max_y = 2010, 2025
        if all_years: min_y, max_y = min(all_years), max(all_years)
        if min_y == max_y: min_y-=1; max_y+=1
        sel_years = st.slider("연도 범위", min_y, max_y, (min_y, max_y))
        sel_diff = st.slider("난이도 (1~5)", 1, 5, (1, 5))
        student_filters = {'exams':sel_exams, 'years':sel_years, 'difficulty':sel_diff, 'keywords':[]}

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
        
        tab1, tab2, tab3, tab4 = st.tabs(["📊 대시보드", "🧮 시뮬레이터 학습", "📝 유형별 기출", "🔥 실전 모의고사"])
        
        # --- [Tab 1] 이론 (단권화 에디터 적용) ---
        with tab1:
            st.caption("📝 텍스트를 더블클릭하거나 버튼을 눌러 나만의 단권화 노트를 만드세요.")
            
            # 1. 데이터 로드 (DB or Default)
            cid = selected_course['course_id']
            chid = current_ch['chapter_id']
            sys_text = current_ch.get('theory_markdown', '')
            
            # Session State 관리 (편집 상태 유지용)
            if "note_blocks" not in st.session_state:
                st.session_state.note_blocks = []
            if "last_loaded" not in st.session_state or st.session_state.last_loaded != f"{cid}_{chid}":
                st.session_state.note_blocks = NoteManager.load_user_notes(USER_ID, cid, chid, sys_text)
                st.session_state.last_loaded = f"{cid}_{chid}"
                # 편집 모드 초기화
                st.session_state.editing_idx = None 

            blocks = st.session_state.note_blocks

            # 2. 블록 렌더링 Loop
            for i, block in enumerate(blocks):
                # 편집 모드인지 확인
                is_editing = (st.session_state.get('editing_idx') == i)
                
                col_content, col_btn = st.columns([0.9, 0.1])
                
                with col_content:
                    if is_editing:
                        # [편집 모드] 텍스트 에디터 표시
                        new_content = st.text_area(f"Block {i}", value=block['content'], height=200, key=f"txt_{i}")
                        c1, c2 = st.columns(2)
                        if c1.button("💾 저장", key=f"save_{i}"):
                            blocks[i]['content'] = new_content
                            blocks[i]['type'] = 'user_edited'
                            NoteManager.save_user_notes(USER_ID, cid, chid, blocks)
                            st.session_state.editing_idx = None # 편집 종료
                            st.rerun()
                        if c2.button("취소", key=f"cancel_{i}"):
                            st.session_state.editing_idx = None
                            st.rerun()
                    else:
                        # [보기 모드] Markdown 표시
                        # 사용자 추가/수정 블록은 배경색을 살짝 다르게 표시 (Highlight)
                        if block.get('type') == 'user_added':
                            st.info(block['content'])
                        elif block.get('type') == 'user_edited':
                            st.warning(block['content']) # 수정됨 표시
                        else:
                            st.markdown(block['content'])
                
                with col_btn:
                    # 도구 버튼 (편집, 삭제)
                    if not is_editing:
                        if st.button("✏️", key=f"edit_btn_{i}", help="수정"):
                            st.session_state.editing_idx = i
                            st.rerun()
                        if st.button("🗑️", key=f"del_btn_{i}", help="삭제(숨김)"):
                            blocks.pop(i)
                            NoteManager.save_user_notes(USER_ID, cid, chid, blocks)
                            st.rerun()
            
            # 3. 새 블록 추가 버튼 (하단)
            st.divider()
            if st.button("➕ 나만의 메모/오답노트 추가하기"):
                # 새 블록 생성
                new_block = {
                    "id": str(uuid.uuid4())[:8],
                    "content": "### 📌 나만의 메모\n여기에 내용을 입력하세요.",
                    "type": "user_added"
                }
                blocks.append(new_block)
                NoteManager.save_user_notes(USER_ID, cid, chid, blocks)
                # 바로 편집 모드로 진입
                st.session_state.editing_idx = len(blocks) - 1
                st.rerun()
            
            # 4. 초기화 버튼 (망쳤을 때)
            if st.button("🔄 원본으로 초기화 (내 메모 삭제)", type="secondary"):
                blocks = NoteManager.parse_markdown_to_blocks(sys_text)
                st.session_state.note_blocks = blocks
                NoteManager.save_user_notes(USER_ID, cid, chid, blocks)
                st.rerun()

        # --- [Tab 2] 시뮬레이터 (Insight 추가 적용) ---
        with tab2:
            sim_type = current_ch.get('simulator_type', 'default')
            defaults = current_ch.get('simulator_defaults', {})
            
            if "bond" in sim_type:
                c1, c2 = st.columns([1,2])
                with c1:
                    f = st.number_input("액면", value=defaults.get('face', 100000))
                    c = st.number_input("표시율", value=defaults.get('crate',0.05))
                    m = st.number_input("시장율", value=defaults.get('mrate',0.08))
                    p = st.slider("기간", 1, 10, 3)
                    
                    # [NEW] 챕터 제목에 '조기상환'이 있으면 추가 옵션 표시 ✨
                    redeem_stats = None
                    if "조기상환" in current_ch['title']:
                        st.markdown("---")
                        st.caption("💰 조기상환 시뮬레이션")
                        r_period = st.slider("상환 시점(연말)", 1, p, min(2, p))
                        r_amt = st.number_input("상환 지급액", value=int(f * 0.98), step=1000)
                        redeem_stats = {'period': r_period, 'amount': r_amt}
                        
                with c2:
                    # 함수에 redeem_stats 전달
                    pv, df, insight = Simulators.bond_basic(f, c, m, p, redeem_stats)
                    st.metric("PV", f"{pv:,}")
                    st.dataframe(df, use_container_width=True)
                    # 상환 분석 결과가 포함된 텍스트 출력
                    if redeem_stats:
                        st.success(insight) # 강조 효과
                    else:
                        st.info(insight)

            elif "entity_equity" in sim_type:
                c1, c2 = st.columns([1,1.5])
                with c1:
                    cost = st.number_input("원가", value=defaults.get('cost',1000))
                    shr = st.number_input("지분", value=defaults.get('share',0.2))
                    ni = st.number_input("순이익", value=defaults.get('net_income',0))
                    dv = st.number_input("배당", value=defaults.get('dividends',0))
                with c2:
                    v, df, insight = Simulators.entity_equity(cost, shr, ni, dv)
                    st.metric("기말장부", f"{v:,}")
                    st.bar_chart(df.set_index("구분")["금액"])
                    st.info(insight)

            elif "depreciation" in sim_type:
                c1, c2 = st.columns([1,2])
                with c1:
                    cost = st.number_input("원가", value=defaults.get('cost', 1000))
                    res = st.number_input("잔존", value=defaults.get('residual', 100))
                    life = st.number_input("내용연수", value=defaults.get('life', 5))
                    rate = None
                    if "db" in sim_type: rate = st.number_input("상각률", value=defaults.get('rate', 0.451))
                    mtd = "DB" if "db" in sim_type else ("SYD" if "syd" in sim_type else "SL")
                with c2:
                    df, insight = Simulators.depreciation(cost, res, life, mtd, rate)
                    st.line_chart(df['기말장부'].str.replace(",","").astype(int))
                    st.dataframe(df, use_container_width=True)
                    st.info(insight)

            elif "inventory" in sim_type:
                c1, c2 = st.columns(2)
                with c1: bq = st.number_input("기초Q", 100); bp = st.number_input("기초P", 100)
                with c2: buyq = st.number_input("매입Q", 100); buyp = st.number_input("매입P", 120)
                sq = st.slider("판매Q", 0, bq+buyq, 150)
                c, e, r1, r2, insight = Simulators.inventory_fifo(bq, bp, buyq, buyp, sq)
                st.success(f"매출원가: {c:,}")
                st.info(f"기말재고: {e:,}")
                st.markdown(insight)

            else: 
                st.info("이론 중심 챕터입니다.")

        # --- [Tab 3] 기출문제 (AI 해설 저장 기능 추가 ✨) ---
        with tab3:
            kws = current_ch.get('related_keywords', [])
            if kws:
                student_filters['keywords'] = kws
                matched = advanced_filter_questions(all_questions_raw, student_filters)
                
                if matched:
                    st.success(f"🔍 조건에 맞는 문제 {len(matched)}개를 찾았습니다.")
                    
                    q_opts = {}
                    for q in matched:
                        year = q.get('exam_info', {}).get('year', '-')
                        etype = q.get('exam_info', {}).get('type', '')
                        q_opts[q['question_id']] = f"[{year} {etype}] {q['topic']}"
                        
                    qid = st.selectbox("문제 선택", list(q_opts.keys()), format_func=lambda x: q_opts[x])
                    q_data = next(q for q in matched if q['question_id'] == qid)
                    
                    st.divider()
                    
                    tags = q_data.get('tags', [])
                    if tags: st.caption("Tags: " + " ".join([f"`#{t}`" for t in tags]))
                    
                    c_q, c_a = st.columns([1.5, 1])
                    
                    # [왼쪽] 문제 및 시뮬레이터
                    with c_q:
                        st.markdown(f"**Q. {q_data['topic']}**")
                        st.markdown(q_data['content_markdown'])
                        
                        opts = q_data.get('choices')
                        if opts:
                            if isinstance(opts, dict): opts = [f"{k}. {v}" for k,v in sorted(opts.items())]
                            st.radio("정답", opts, label_visibility="collapsed")
                            
                        # 시뮬레이터
                        sim_config = q_data.get('sim_config')
                        if sim_config:
                            st.write("---")
                            with st.expander(f"🧪 {sim_config.get('label', '시뮬레이터로 검증하기')}"):
                                s_type = sim_config.get('type')
                                p = sim_config.get('params', {})
                                
                                # 1. Bond
                                if s_type == "bond_basic":
                                    f_val = st.number_input("액면", value=p.get('face', 100000), key=f"s_{qid}_f")
                                    c_val = st.number_input("표시이자", value=p.get('crate', 0.05), format="%.2f", key=f"s_{qid}_c")
                                    m_val = st.number_input("유효이자", value=p.get('mrate', 0.08), format="%.2f", key=f"s_{qid}_m")
                                    
                                    # [수정] insight unpack & display
                                    res_p, res_df, insight = Simulators.bond_basic(f_val, c_val, m_val, p.get('periods', 3))
                                    st.dataframe(res_df, use_container_width=True)
                                    st.info(insight)
                                    
                                # 2. Depreciation
                                elif s_type == "depreciation":
                                    c_val = st.number_input("취득원가", value=p.get('cost', 1000), key=f"s_{qid}_cost")
                                    r_val = st.number_input("잔존가치", value=p.get('residual', 0), key=f"s_{qid}_res")
                                    l_val = st.number_input("내용연수", value=p.get('life', 5), key=f"s_{qid}_life")
                                    rate_val = p.get('rate')
                                    method_val = p.get('method', 'SL')
                                    
                                    df, insight = Simulators.depreciation(c_val, r_val, l_val, method_val, rate_val)
                                    st.line_chart(df['기말장부'].str.replace(",","").astype(int))
                                    st.dataframe(df, use_container_width=True)
                                    st.info(insight)
                                    
                                # 3. Inventory
                                elif s_type == "inventory_fifo":
                                    bq = p.get('base_qty', 100); bp = p.get('base_price', 100)
                                    buyq = p.get('buy_qty', 100); buyp = p.get('buy_price', 120)
                                    sell_q = st.slider("판매수량 시뮬레이션", 0, bq+buyq, p.get('sell_qty', 150), key=f"s_{qid}_sell")
                                    
                                    cogs, end, r1, r2, insight = Simulators.inventory_fifo(bq, bp, buyq, buyp, sell_q)
                                    st.success(f"매출원가: {cogs:,}")
                                    st.info(f"기말재고: {end:,}")
                                    st.caption(insight)

                                # 4. Entity
                                elif s_type == "entity_equity":
                                    c_cost = st.number_input("취득원가", value=p.get('cost', 1000000), key=f"s_{qid}_ec")
                                    c_share = st.number_input("지분율", value=p.get('share', 0.2), key=f"s_{qid}_es")
                                    c_ni = st.number_input("순이익", value=p.get('net_income', 0), key=f"s_{qid}_eni")
                                    c_div = st.number_input("배당금", value=p.get('dividends', 0), key=f"s_{qid}_ediv")
                                    
                                    ebv, edf, insight = Simulators.entity_equity(c_cost, c_share, c_ni, c_div)
                                    st.metric("기말 장부금액", f"{ebv:,}")
                                    st.bar_chart(edf.set_index("구분")["금액"])
                                    st.info(insight)

                    # [오른쪽] 해설 (AI 저장 기능 적용)
                    with c_a:
                        # 해설 펼침 상태: 이미 해설이 있으면 펼쳐둠
                        has_solution = bool(q_data.get('solution_steps') or q_data.get('steps'))
                        with st.expander("💡 해설 보기", expanded=has_solution):
                            st.info(f"정답: {q_data.get('answer', '?')}")
                            
                            sols = q_data.get('solution_steps') or q_data.get('steps')
                            
                            if sols:
                                # 저장된 해설이 있는 경우 바로 표시
                                for s in sols:
                                    st.markdown(f"**{s.get('title','Step')}**")
                                    st.caption(s.get('content',''))
                                    st.divider()
                            else:
                                st.warning("등록된 해설이 없습니다.")
                                
                                # AI 해설 요청 버튼
                                if GEMINI_AVAILABLE:
                                    if st.button("🤖 AI 해설 요청 및 저장", key=f"ai_btn_{qid}"):
                                        with st.spinner("AI가 해설을 작성하고 DB에 저장 중입니다..."):
                                            try:
                                                model = genai.GenerativeModel("gemini-2.5-flash")
                                                # 구조화된 답변을 유도하는 프롬프트
                                                prompt = f"""
                                                문제: {q_data['content_markdown']}
                                                위 문제에 대해 초심자도 이해하기 쉬운 단계별 해설을 작성해줘.
                                                형식은 자유롭게 하되, 마크다운을 적절히 사용해.
                                                """
                                                response = model.generate_content(prompt)
                                                ai_text = response.text
                                                
                                                # DB에 저장할 포맷으로 변환
                                                new_solution = [
                                                    {
                                                        "title": "🤖 AI 선생님의 해설", 
                                                        "content": ai_text
                                                    }
                                                ]
                                                
                                                # Firestore 저장
                                                if update_question_solution(qid, new_solution):
                                                    st.success("해설이 저장되었습니다! 새로고침합니다.")
                                                    load_questions.clear() # 캐시 초기화 (중요)
                                                    st.rerun() # 화면 새로고침하여 해설 표시
                                                
                                            except Exception as e:
                                                st.error(f"오류 발생: {e}")
                                else:
                                    st.caption("AI 기능을 사용하려면 API 키가 필요합니다.")
                else:
                    st.warning("조건에 맞는 문제가 없습니다.")
            else:
                st.info("이 챕터에는 연결된 태그가 없습니다.")

        # --- [Tab 4] 실전 모의고사 (새로 추가된 부분 ✨) ---
        with tab4:
            st.header("🔥 실전 모의고사 (Exam Mode)")
            st.caption("실제 시험처럼 연도별로 문제를 순서대로 풀어봅니다.")

            # 1. 시험지 선택 (Filter)
            # 데이터에서 존재하는 연도와 유형 추출
            available_years = sorted(list(set([q.get('exam_info', {}).get('year') for q in all_questions_raw if q.get('exam_info', {}).get('year')])), reverse=True)
            available_types = sorted(list(set([q.get('exam_info', {}).get('type') for q in all_questions_raw if q.get('exam_info', {}).get('type')])))

            c_filter1, c_filter2, c_btn = st.columns([1, 1, 1])
            with c_filter1:
                sel_year = st.selectbox("연도 선택", available_years)
            with c_filter2:
                sel_type = st.selectbox("시험 유형", available_types)
            
            # 2. 문제 데이터 로드
            exam_questions = get_exam_questions(all_questions_raw, sel_type, sel_year)
            
            if not exam_questions:
                st.warning("조건에 맞는 문제가 없습니다.")
            else:
                # 3. 네비게이션 (Session State 사용)
                if 'exam_idx' not in st.session_state:
                    st.session_state.exam_idx = 0
                
                # 시험지가 바뀌면 인덱스 초기화 (안전장치)
                # (구현 팁: 단순화를 위해 여기서는 생략하나, 필요 시 로직 추가 가능)

                total_q = len(exam_questions)
                curr_idx = st.session_state.exam_idx
                
                # 인덱스 범위 보정
                if curr_idx >= total_q: curr_idx = total_q - 1
                if curr_idx < 0: curr_idx = 0
                
                q_data = exam_questions[curr_idx]
                qid = q_data['question_id']

                # --- 상단 네비게이션 바 ---
                c_prev, c_info, c_next = st.columns([1, 2, 1])
                with c_prev:
                    if st.button("⬅️ 이전 문제", disabled=(curr_idx == 0), key="btn_prev"):
                        st.session_state.exam_idx -= 1
                        st.rerun()
                with c_info:
                    st.markdown(f"<h4 style='text-align: center;'>제 {curr_idx + 1} 번 / 총 {total_q} 문항</h4>", unsafe_allow_html=True)
                with c_next:
                    if st.button("다음 문제 ➡️", disabled=(curr_idx == total_q - 1), key="btn_next"):
                        st.session_state.exam_idx += 1
                        st.rerun()
                
                st.progress((curr_idx + 1) / total_q)
                st.divider()

                # 4. 문제 풀이 영역
                col_q, col_solve = st.columns([1.2, 1])
                
                # [왼쪽] 지문 및 보기
                with col_q:
                    st.badge(q_data['topic'])
                    st.markdown(q_data['content_markdown'])
                    
                    # 보기 출력
                    opts = q_data.get('choices', {})
                    user_ans = st.radio("정답 선택", [f"{k}. {v}" for k,v in sorted(opts.items())], key=f"exam_radio_{qid}")

                # [오른쪽] 정답 확인 및 해설
                with col_solve:
                    st.info("💡 문제를 푼 뒤 아래 버튼을 눌러 확인하세요.")
                    
                    # 정답 확인 토글
                    with st.expander("✅ 정답 및 해설 확인", expanded=False):
                        ans = q_data.get('answer', 0)
                        st.markdown(f"### 정답: **{ans}번**")
                        
                        if str(ans) in user_ans:
                            st.success("🎉 정답입니다!")
                        else:
                            st.error("앗, 틀렸습니다. 다시 풀어보세요.")

                        st.markdown("---")
                        
                        # (A) 저장된 해설 표시
                        solutions = q_data.get('solution_steps', [])
                        if solutions:
                            for s in solutions:
                                st.markdown(f"**{s.get('title')}**")
                                st.caption(s.get('content'))
                                st.divider()
                        else:
                            st.warning("등록된 해설이 없습니다.")
                            # (B) AI 해설 요청 버튼 (기존 로직 재사용)
                            if GEMINI_AVAILABLE:
                                if st.button("🤖 AI 해설 요청 (DB저장)", key=f"exam_ai_{qid}"):
                                    # ... (AI 해설 요청 코드: 위에서 만든 코드 그대로 사용) ...
                                    pass 

                    # 시뮬레이터 (필요시 열어보기)
                    sim_conf = q_data.get('sim_config')
                    if sim_conf:
                        with st.expander(f"🧪 시뮬레이터로 검증 ({sim_conf.get('type')})"):
                            # 기존 시뮬레이터 렌더링 로직 재사용
                            # Tab 3의 시뮬레이터 렌더링 코드를 함수화해서 호출하거나, 
                            # 여기서 간단히 params만 받아서 Simulators 클래스 호출
                            pass

# ---------------------------------------------------------
# [B] 관리자 모드 (Admin)
# ---------------------------------------------------------
elif mode == "🛠️ 관리자 모드 (Admin)":
    st.header("🛠️ 통합 관리 센터")
    tab_course, tab_quest = st.tabs(["📚 커리큘럼 관리", "📥 문제/해설 통합 관리"])
    
    # 1. 커리큘럼
    with tab_course:
        st.markdown("#### 1️⃣ 등록된 코스 목록")
        if all_courses:
            df_c = pd.DataFrame(all_courses)
            df_view = df_c[['course_id', 'engine_type', 'title']].copy()
            df_view['chapters_count'] = df_c['chapters'].apply(lambda x: len(x) if isinstance(x, list) else 0)
            gb = GridOptionsBuilder.from_dataframe(df_view)
            gb.configure_selection('single', use_checkbox=True)
            gb.configure_column("course_id", width=100); gb.configure_column("title", width=300)
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
                    st.success("저장 완료"); load_courses.clear(); st.rerun()
                except Exception as e: st.error(e)
        with c2:
            if selected and st.button("🗑️ 삭제"):
                delete_document("courses", selected[0]['course_id'])
                st.success("삭제 완료"); load_courses.clear(); st.rerun()

    # 2. 문제/해설 통합
    with tab_quest:
        st.markdown("#### 2️⃣ 등록된 문제 목록 (복수 선택/삭제)")
        if all_questions_raw:
            df_q = pd.DataFrame(all_questions_raw)
            if 'exam_info' not in df_q.columns: df_q['exam_info'] = None
            if 'tags' not in df_q.columns: df_q['tags'] = None
            if 'engine_type' not in df_q.columns: df_q['engine_type'] = '-'
            if 'topic' not in df_q.columns: df_q['topic'] = '제목 없음'
            if 'sim_config' not in df_q.columns: df_q['sim_config'] = None
            
            df_q['year'] = df_q['exam_info'].apply(lambda x: x.get('year', 0) if isinstance(x, dict) else 0)
            df_q['exam'] = df_q['exam_info'].apply(lambda x: x.get('type', '-') if isinstance(x, dict) else '-')
            df_q['tags_str'] = df_q['tags'].apply(lambda x: ", ".join(x) if isinstance(x, list) else "")
            df_q['has_sol'] = df_q.apply(lambda r: "O" if (r.get('solution_steps') or r.get('steps')) else "X", axis=1)
            df_q['has_sim'] = df_q.apply(lambda r: "⚡" if r.get('sim_config') else "-", axis=1)
            
            df_grid = df_q[['question_id', 'year', 'exam', 'engine_type', 'topic', 'tags_str', 'has_sol', 'has_sim']].copy()
            
            gb_q = GridOptionsBuilder.from_dataframe(df_grid)
            gb_q.configure_selection('multiple', use_checkbox=True)
            gb_q.configure_pagination(paginationAutoPageSize=False, paginationPageSize=10)
            gb_q.configure_column("question_id", width=100, pinned=True)
            gb_q.configure_column("topic", width=250)
            gb_q.configure_column("has_sim", header_name="Sim", width=50, cellStyle={'textAlign': 'center'})
            
            gridOpts_q = gb_q.build()
            grid_resp_q = AgGrid(df_grid, gridOptions=gridOpts_q, update_mode=GridUpdateMode.SELECTION_CHANGED, fit_columns_on_grid_load=True, height=350, key='admin_q_grid')
            
            sel_q = grid_resp_q['selected_rows']
            if isinstance(sel_q, pd.DataFrame): sel_q = sel_q.to_dict('records')
        else:
            st.info("문제가 없습니다."); sel_q = []
            
        st.divider()
        target_q_data = {}
        header_text_q = "🆕 신규 문제 등록"
        if sel_q:
            count = len(sel_q)
            last_sel_id = sel_q[0]['question_id'] 
            target_q_data = next((q for q in all_questions_raw if q['question_id'] == last_sel_id), {})
            if count == 1: header_text_q = f"✏️ 수정 모드: {last_sel_id}"
            else: header_text_q = f"✅ {count}개 선택됨 (편집은 첫 번째 항목 기준)"
            
        st.subheader(header_text_q)
        
        # 1. 메인 데이터 (문제 정보)
        # 해설(solution_steps)은 여기서 제외하고 보여줄 수도 있지만, 
        # 일단 전체를 다루는 마스터 JSON 창은 유지합니다.
        default_val_q = json.dumps(target_q_data, indent=2, ensure_ascii=False) if target_q_data else ""
        
        with st.expander("📝 전체 JSON 데이터 (고급 사용자용)", expanded=False):
            q_json = st.text_area("Master JSON", value=default_val_q, height=300)

        # 2. [NEW] 해설 전용 입력창 (편의 기능) ✨
        with qc1:

            # [수정] 해설 전용 입력창 및 스마트 저장 로직
            st.markdown("#### 💡 스마트 해설(Solution) 등록기")
            st.caption("AI 프롬프트 결과(JSON List)를 여기에 붙여넣으세요. ID가 포함되어 있으면 알아서 제자리를 찾아갑니다.")
            
            # 현재 선택된 문제의 기존 해설을 기본값으로 표시 (없으면 빈칸)
            current_sol = target_q_data.get('solution_steps', [])
            default_sol = json.dumps(current_sol, indent=2, ensure_ascii=False) if current_sol else ""
            
            sol_json_input = st.text_area("Solution JSON Input", value=default_sol, height=300)

            if st.button("💾 해설 데이터 저장 (Smart Save)"):
                try:
                    if not sol_json_input.strip():
                        st.warning("입력된 데이터가 없습니다.")
                        st.stop()

                    input_data = json.loads(sol_json_input)

                    # 데이터 유효성 검사: 리스트여야 함
                    if not isinstance(input_data, list):
                        input_data = [input_data] # 리스트가 아니면 리스트로 감쌈

                    if not input_data:
                        st.warning("빈 리스트입니다.")
                        st.stop()

                    # --- [판단 로직] 이것이 '배치 파일'인가? '단일 해설'인가? ---
                    first_item = input_data[0]
                    success_count = 0
                    
                    # Case A: 배치 모드 (JSON 안에 'question_id'가 있는 경우)
                    # 예: [{"question_id": "Q41", "solution_steps": [...]}, {"question_id": "Q42", ...}]
                    if "question_id" in first_item and "solution_steps" in first_item:
                        progress_bar = st.progress(0)
                        for i, item in enumerate(input_data):
                            target_id = item.get("question_id")
                            new_steps = item.get("solution_steps")
                            
                            if target_id and new_steps:
                                # 해당 ID를 가진 문서를 찾아 업데이트
                                db.collection("questions").document(str(target_id)).update({
                                    "solution_steps": new_steps
                                })
                                success_count += 1
                            progress_bar.progress((i + 1) / len(input_data))
                        
                        st.success(f"총 {success_count}개의 문제에 해설을 배포(Update)했습니다!")
                    
                    # Case B: 단일 모드 (JSON이 바로 해설 단계들인 경우)
                    # 예: [{"title": "Step 1", "content": "..."}, {"title": "Step 2", ...}]
                    elif "title" in first_item and "content" in first_item:
                        # 이때는 Grid에서 '선택된 문제(target_q_data)'에만 저장해야 함
                        if target_q_data:
                            target_id = target_q_data['question_id']
                            db.collection("questions").document(str(target_id)).update({
                                "solution_steps": input_data
                            })
                            st.success(f"ID: {target_id} 문제의 해설을 업데이트했습니다.")
                        else:
                            st.error("단일 해설 모드입니다. 먼저 왼쪽 표에서 해설을 넣을 문제를 선택해주세요.")
                    
                    else:
                        st.error("알 수 없는 JSON 형식입니다. 'question_id'가 포함된 객체 리스트이거나, 'title/content'가 포함된 해설 리스트여야 합니다.")

                    # 캐시 초기화 및 새로고침
                    load_questions.clear()
                    time.sleep(1.5) # 메시지 읽을 시간 줌
                    st.rerun()

                except json.JSONDecodeError:
                    st.error("JSON 형식이 올바르지 않습니다. 따옴표나 콤마를 확인해주세요.")
                except Exception as e:
                    st.error(f"저장 중 오류 발생: {e}")