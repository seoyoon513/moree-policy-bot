# ───────────────────────────────────────────────────────────────
# 정책 봇 이미지. API 서버와 디스코드 봇이 '같은 이미지'를 쓰고
# 실행 명령(command)만 다르게 띄운다 → docker-compose.yml 참고
# ───────────────────────────────────────────────────────────────
FROM python:3.12-slim

# 파이썬 로그가 버퍼링 없이 바로 `docker logs`에 찍히도록 / .pyc 안 만들기
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Seoul

WORKDIR /app

# 1) 의존성 먼저 설치 → 코드만 바뀌면 이 레이어는 캐시 재사용 (빌드가 빨라짐)
COPY requirements.txt .
RUN pip install -r requirements.txt

# 2) 코드 복사 (.dockerignore에 있는 .env, data/, .venv 는 들어가지 않음)
COPY . .

# 3) root가 아닌 사용자로 실행 (컨테이너가 뚫려도 권한 최소화)
RUN useradd --create-home --uid 1000 app && mkdir -p /app/data && chown -R app:app /app
USER app

# 컨테이너 '안'에서 열리는 포트. 문서 역할일 뿐, 바깥 공개는 compose의 ports가 결정
EXPOSE 8000

# 기본 실행 = API 서버. 0.0.0.0 으로 열어야 컨테이너 밖(다른 컨테이너/호스트)에서 접속 가능
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
