# RAG 평가셋

`golden.jsonl` 한 줄 = 질문 하나.

| 필드 | 의미 |
|---|---|
| `question` | 사용자가 실제로 칠 법한 질문 (문서 표현을 그대로 베끼지 말 것) |
| `expected_titles` | 정답 결정의 **주제(제목)에 포함된 문자열**. 하나라도 걸리면 정답. 소스(Notion/jsonl)가 바뀌어도 id와 무관하게 동작 |
| `must_include` | 답변에 반드시 들어가야 할 핵심 단어 (답변 평가용) |
| 빈 `expected_titles` | **문서에 없는 질문**. 검색 결과가 0건이어야 정답 (하한선 검증용) |

## 늘리는 법
- 새 결정이 승인될 때마다 질문 1~2개를 추가한다. 목표 30개 이상.
- 실제로 멘션 질문으로 들어온 질문 중 틀린 것을 그대로 옮겨 오는 것이 가장 좋은 평가 데이터.
- 정답이 없는 질문(n로 시작)을 전체의 20~30% 정도 섞는다.

## 실행
```bash
python -m scripts.eval_rag                 # 검색 평가만 (임베딩 비용만, 거의 0원)
python -m scripts.eval_rag --sweep         # 하한선(RAG_MIN_SCORE)별 성능 비교
python -m scripts.eval_rag --answers       # 답변까지 생성해서 평가 (Claude 토큰 사용)
python -m scripts.eval_rag --answers --judge   # + LLM 채점 (토큰 2배)
```
결과는 `eval/results/<시각>.json`에 쌓인다. 설정을 바꾸기 전/후 결과를 비교해 개선 여부를 판단한다.
