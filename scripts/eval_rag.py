"""RAG 정확도 + 토큰 비용 평가.

    python -m scripts.eval_rag                    # 검색만
    python -m scripts.eval_rag --sweep            # 하한선별 비교 → RAG_MIN_SCORE 고르기
    python -m scripts.eval_rag --answers          # 답변 생성까지
    python -m scripts.eval_rag --answers --judge  # + LLM 채점
    python -m scripts.eval_rag --k 3 --min-score 0.35   # 설정 바꿔서 실험

먼저 인덱스가 있어야 한다 (python -m scripts.ingest). 결과는 eval/results/에 저장.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from anthropic import Anthropic

from app import usage as usage_log
from app.config import get_settings
from app.evaluation import (aggregate_answers, aggregate_retrieval, answer_scores, by_type, load_golden,
                            retrieval_scores)
from app.rag.embedder import build_embedder
from app.rag.generator import AnswerGenerator, build_context
from app.rag.retriever import Retriever
from app.rag.store import VectorStore

JUDGE_PROMPT = """너는 RAG 답변 채점자다. <context>의 문서와 <answer>를 비교해 JSON만 출력한다.
- faithfulness (1~5): 답변의 모든 주장이 context에 근거하는가. 근거 없는 내용이 하나라도 있으면 3 이하.
- relevance (1~5): 질문에 직접 답했는가.
- reason: 한 문장.
출력 예: {"faithfulness": 5, "relevance": 4, "reason": "..."}"""

SWEEP = [0.0, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5]


def judge(client: Anthropic, model: str, question: str, hits, answer: str) -> dict:
    with usage_log.timer() as elapsed:
        msg = client.messages.create(
            model=model, max_tokens=300, system=JUDGE_PROMPT,
            messages=[{"role": "user", "content": f"{build_context(hits)}\n\n<question>{question}</question>\n<answer>{answer}</answer>"}],
        )
    usage_log.from_anthropic("judge", model, msg, elapsed())
    text = "".join(b.text for b in msg.content if b.type == "text")
    try:
        d = json.loads(text[text.find("{"): text.rfind("}") + 1])
        return {"faithfulness": int(d["faithfulness"]), "relevance": int(d["relevance"]), "reason": d.get("reason", "")}
    except (ValueError, KeyError):
        return {}


def print_by_type(table: dict[str, dict], keys: list[str]) -> None:
    print("\n 유형      " + "  ".join(f"{k:<12}" for k in keys) + "  문항")
    for t, a in table.items():
        n = a.get("n", "")
        print(f" {t:<9} " + "  ".join(f"{a.get(k)!s:<12}" for k in keys) + f"  {n}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", default="eval/golden.jsonl")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--min-score", type=float, default=None, help="기본값: .env의 RAG_MIN_SCORE")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--answers", action="store_true")
    ap.add_argument("--judge", action="store_true")
    ap.add_argument("--rerank", action="store_true", help="LLM 리랭커를 켜고 평가 (RERANK_* 설정 사용)")
    args = ap.parse_args()

    s = get_settings()
    min_score = s.rag_min_score if args.min_score is None else args.min_score
    items = load_golden(args.golden)
    store = VectorStore(s.chroma_path, s.chroma_collection)
    if store.count() == 0:
        raise SystemExit("인덱스가 비어 있어요. 먼저 python -m scripts.ingest 를 실행하세요.")
    if args.rerank:
        if not s.anthropic_api_key:
            raise SystemExit("리랭킹에는 ANTHROPIC_API_KEY가 필요해요.")
        from app.rag.reranker import LLMReranker
        # 리랭커가 순위와 거절을 정하므로 벡터 하한선은 후보를 넓게 받는 용도(rerank_min_score)로만 쓰고,
        # 아래 공통 하한선 적용은 끈다
        retriever = Retriever(build_embedder(s), store, min_score=s.rerank_min_score,
                              reranker=LLMReranker(s.anthropic_api_key, s.rerank_model, s.rerank_min_relevance),
                              rerank_candidates=s.rerank_candidates)
        min_score = 0.0
        args.sweep = False
    else:
        # 하한선은 아래에서 직접 적용 (sweep 때 임베딩을 한 번만 하려고)
        retriever = Retriever(build_embedder(s), store, min_score=0.0)
    started = datetime.now(timezone.utc)

    raw = {it.id: retriever.search(it.question, k=args.k, area=it.area) for it in items}

    def run_retrieval(th: float):
        rows = []
        for it in items:
            hits = [h for h in raw[it.id] if h.score >= th]
            r = retrieval_scores(it, [h.title for h in hits])
            r.update(id=it.id, question=it.question, results=[(h.title, h.score) for h in hits])
            rows.append(r)
        return rows

    rows = run_retrieval(min_score)
    rr = getattr(retriever, "reranker", None)
    if rr:
        print(f"리랭커 호출 {rr.calls}회 중 파싱 실패(벡터 순서로 대체) {rr.fallbacks}회")
        if rr.last_raw:
            print(f"  마지막 실패 응답: {rr.last_raw[:300]!r}")
    report = {"run_at": started.isoformat(), "k": args.k, "min_score": min_score, "rerank": args.rerank,
              "rerank_fallbacks": rr.fallbacks if rr else None,
              "rerank_last_failed_raw": rr.last_raw if rr else None,
              "rerank_model": s.rerank_model if args.rerank else None,
              "embedding": s.embedding_backend, "llm_model": s.llm_model,
              "retrieval": aggregate_retrieval(rows), "retrieval_by_type": by_type(items, rows, aggregate_retrieval),
              "items": rows}

    mode = f"리랭킹 {s.rerank_model}, 후보 {s.rerank_candidates}" if args.rerank else f"min_score={min_score}"
    print(f"\n=== 검색 평가 (k={args.k}, {mode}, {len(items)}문항) ===")
    for r in rows:
        mark = ("✅" if r["rejected"] else "❌") if r["negative"] else ("✅" if r["hit"] else "❌")
        top = ", ".join(f"{t[:18]}({sc:.2f})" for t, sc in r["results"][:3]) or "(결과 없음)"
        print(f"{mark} {r['id']} {r['question'][:28]:<28} → {top}")
    print(json.dumps(report["retrieval"], ensure_ascii=False, indent=2))
    print_by_type(report["retrieval_by_type"], ["hit_at_k", "top1", "reject_acc"])

    if args.sweep:
        print("\n=== 하한선 스윕 ===\n min_score  hit@k   top1   MRR  거절정확도  전체정확도  평균결과수")
        sweep = {}
        for th in SWEEP:
            a = aggregate_retrieval(run_retrieval(th))
            sweep[th] = a
            print(f"   {th:<6}   {a['hit_at_k']!s:<6} {a['top1']!s:<6} {a['mrr']!s:<6} "
                  f"{a['reject_acc']!s:<10} {a['overall_acc']!s:<10} {a['avg_results']}")
        report["sweep"] = sweep
        # 점수가 정답/오답을 얼마나 잘 가르는지 보려면 이 두 분포를 비교
        pos_scores = [h.score for it in items if not it.is_negative for h in raw[it.id]
                      if any(e in h.title for e in it.expected_titles)]
        neg_top = [raw[it.id][0].score for it in items if it.is_negative and raw[it.id]]
        if pos_scores and neg_top:
            print(f"\n정답 문서 점수 최저 {min(pos_scores):.3f} / 없는 질문의 1위 점수 최고 {max(neg_top):.3f}"
                  f"  → 이 사이에 하한선을 두면 둘 다 맞출 수 있음" if min(pos_scores) > max(neg_top)
                  else f"\n정답 최저 {min(pos_scores):.3f} ≤ 오답 최고 {max(neg_top):.3f} → 하한선만으로는 분리 불가 (리랭킹/하이브리드 검색 필요)")

    if args.answers:
        if not s.anthropic_api_key:
            raise SystemExit("ANTHROPIC_API_KEY가 필요해요.")
        gen = AnswerGenerator(s.anthropic_api_key, s.llm_model, s.llm_max_tokens)
        client = Anthropic(api_key=s.anthropic_api_key) if args.judge else None
        ans_rows = []
        print("\n=== 답변 평가 ===")
        for it in items:
            hits = [h for h in raw[it.id] if h.score >= min_score]
            res = gen.answer(it.question, hits)
            a = answer_scores(it, res.text)
            if args.judge and hits and not it.is_negative:
                a.update(judge(client, s.llm_model, it.question, hits, res.text))
            a.update(id=it.id, answer=res.text,
                     tokens=(res.usage.input_tokens, res.usage.output_tokens) if res.usage else (0, 0))
            ans_rows.append(a)
            ok = a["refused"] if a["negative"] else a["keywords_ok"] and a["cited"]
            extra = f" 누락={a['missing']}" if a.get("missing") else ""
            extra += f" 채점={a['faithfulness']}/{a['relevance']}" if "faithfulness" in a else ""
            print(f"{'✅' if ok else '❌'} {it.id} tokens in/out={a['tokens'][0]}/{a['tokens'][1]}{extra}")
        report["answers"] = aggregate_answers(ans_rows)
        report["answers_by_type"] = by_type(items, ans_rows, aggregate_answers)
        report["answer_items"] = ans_rows
        print(json.dumps(report["answers"], ensure_ascii=False, indent=2))
        print_by_type(report["answers_by_type"], ["keyword_acc", "citation_rate", "reject_acc"])

    # 이번 실행에서 쓴 토큰/비용 (usage.jsonl에서 실행 시작 이후 기록만)
    spent = [r for r in usage_log.load() if r["ts"] >= started.isoformat()]
    cost = {}
    for r in spent:
        c = cost.setdefault(r["feature"], {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
        c["calls"] += 1
        c["input_tokens"] += r["input_tokens"]
        c["output_tokens"] += r["output_tokens"]
        c["cost_usd"] = round(c["cost_usd"] + r["cost_usd"], 6)
    report["cost"] = cost
    report["cost_total_usd"] = round(sum(c["cost_usd"] for c in cost.values()), 6)
    if args.answers:
        answered = cost.get("answer", {}).get("calls", 0)
        report["cost_per_answer_usd"] = round(cost.get("answer", {}).get("cost_usd", 0) / answered, 6) if answered else 0
    print(f"\n이번 실행 비용: ${report['cost_total_usd']:.4f}  {json.dumps(cost, ensure_ascii=False)}")

    out = Path("eval/results") / f"{started.astimezone().strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"저장: {out}")


if __name__ == "__main__":
    main()
