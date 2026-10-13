# 소설 지도 (Novel Map)

원고(txt)를 올리면 **인물 관계도 · 사건 타임라인 · 로어북(설정집)** 을 만들고, 모든 요소를 원문 근거 장면으로 연결하며, 로어북을 마크다운으로 내려받게 해 주는 도구.
FastAPI + LangGraph + Chroma(RAG) + 공급사 선택형 LLM API + 정적 HTML/D3. 기획 문서: `소설 지도 (Novel Map) 기획 문서.md`

## 실행

```bash
pip install -r requirements.txt
python -m uvicorn app.main:app --port 8000      # http://localhost:8000
```
1. 화면 상단 **LLM 설정 바**에서 공급사·모델을 고르고 API 키를 넣어 “저장·연결 테스트”를 누른다. 키가 없으면 “분석 실행”은 꺼져 있다.
   - 키는 서버 메모리에만 보관한다(파일·로그·JSON 캐시·LangGraph State에 쓰지 않음, 재시작하면 다시 입력). 환경 변수(`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`)에 있으면 입력 없이 기본값으로 쓴다. `.env`는 `.gitignore`에 들어 있다.
2. 작품을 고르고 “분석 실행”. 이미 분석한 결과(`data/results/*.json`)는 키 없이도 지도·로어북·내려받기가 뜬다.
3. 시연용 사전 분석: `python scripts/precompute.py [novel_id] [txt경로] [--fresh]` (키는 환경 변수/.env)

## LLM 공급사 (`app/llm/models.json`)
| 공급사 | 상태 | 비고 |
|---|---|---|
| Anthropic | 구현 | 기본 Claude Haiku 5.5, 선택 Sonnet 5.5. 구조화 출력(JSON 스키마), 샘플링 파라미터 미전송, 추출 노드는 생각 모드 끔·effort `low` |
| Google | 구현 | Gemini OpenAI 호환 엔드포인트. 모델 ID는 `gemini-3.8-flash`만 확인됨. 무료 티어는 일일 호출 한도가 매우 낮음 |
| OpenAI | 준비 중 | 어댑터 코드(`openai_compat.py`)는 공통. 공식 문서에서 모델 ID를 확인해 `models.json`에 넣고 `ready`를 `true`로 |

노드는 공급사를 모른다: 모든 호출은 `generate_json(prompt, schema)` 하나를 거치고 Pydantic으로 검증한다. 429는 SDK가 지수 대기 후 재시도하고 병렬 수는 `LLM_CONCURRENCY`(기본 Anthropic 8, 그 외 2)로 제한한다.

## 구조
| 경로 | 역할 |
|---|---|
| `app/rag/` | splitter(장→장면, 150~600토큰, 한글 보정), store(Chroma + bge-m3), retriever(인접 장면 확장) |
| `app/graph/` | LangGraph: load_scenes → extract(Send 병렬) → normalize → lore → validate → save. 검증 실패 시 실패 장면만 extract로 재시도(최대 2회) |
| `app/llm/` | 공급사 어댑터(`anthropic.py`, `openai_compat.py`), 설정/키(`base.py`), `models.json` |
| `app/cache.py` | 장면별 추출 결과 캐시(`data/results/{id}.extractions.jsonl`): 한도 초과로 중단돼도 이어서 실행, 정규화만 재실행하면 LLM 호출 0회 |
| `app/routers/` | `/novels`, `/analyze`(+SSE), `/graph` `/timeline` `/scenes/{id}`, `/lore` `/lore/export`, `/ask`, `/llm/providers` `/llm/config` |
| `web/` | 설정 바 + 탭 3개(관계도·타임라인·로어북) + 근거 패널 + 질문 채팅 |

- **임베딩**: 로컬 `BAAI/bge-m3`(sentence-transformers). 첫 사용 때 모델을 내려받는다(약 2GB). 실패하면 해시 임베딩으로 대체되며, 테스트는 `EMBEDDING=hash`.
- **로어북**: 인물·장소·조직·물건·용어 카드. 속성마다 근거 장면 ID와 원문 인용이 붙고, 검증 노드가 근거 없는 속성을 제거한다. 요약은 속성+장면 발췌만 입력으로 쓴다(근거 외 내용 금지 프롬프트). 첫 등장·등장 수·함께 나온 항목은 코드로 계산한다.
- **관계도**: 대분류 6종(가족·연애·혼인·협력·동료·위계·소속·적대·갈등·기타·중립) = 선 색, 세부 label = 호버, 방향 있는 관계는 화살표.
- 인물 중복 수동 보정: `data/results/{novel_id}.overrides.json` → `{"merge": {"별칭": "대표이름"}, "drop": ["제외할 이름"]}` 후 재분석(캐시 덕분에 호출 없이 정규화만 다시 돈다).

## 점검
`EMBEDDING=hash python scripts/smoke_test.py` — API 키 없이 가짜 LLM 응답으로 파이프라인·캐시 재실행·API·마크다운 내보내기를 검증한다.

## 현재 상태
- 샘플 원문: `data/novels/baskervilles.txt`(15장, 155장면), 한글 예시 `mujeong.txt`(무정 1~40장), `guunmong.txt`(구운몽, 옛한글 변환본).
- 이전 스키마(복선 판정 포함)로 만든 결과는 `data/results/legacy_v1/`에 보관했다. 새 구조의 결과는 API 키를 넣고 다시 분석해야 생성된다.
- 실제 LLM으로 전체 분석한 결과는 아직 없다(키 필요). 파이프라인은 가짜 응답으로만 검증했다.
