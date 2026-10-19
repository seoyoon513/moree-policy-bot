import os

# 테스트가 실제 data/usage.jsonl에 기록을 남기지 않도록 (필요한 테스트만 fixture로 켠다)
os.environ.setdefault("USAGE_LOG_DISABLED", "1")
# .env 값이 테스트에 섞이지 않도록 (load_dotenv는 이미 있는 환경변수를 덮어쓰지 않는다)
os.environ["API_KEY"] = ""
os.environ["RERANK_ENABLED"] = "false"
