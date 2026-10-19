# 서버 배포 (Docker Compose)

구성: 클라우드 VM 1대 위에 컨테이너 2개.

```
인터넷 ──(공개 IP)── [VM: Ubuntu]
                      ├─ docker network (compose 기본 네트워크)
                      │   ├─ api  : uvicorn :8000  ← 호스트의 127.0.0.1:8000 에만 연결
                      │   └─ bot  : python bot.py  → http://api:8000 (내부 DNS)
                      │                             → Discord Gateway (나가는 WebSocket)
                      └─ ./data  (볼륨: chroma 인덱스, history.jsonl, usage.jsonl)
```
들어오는 포트는 **SSH(22)만** 열면 된다. 봇은 디스코드로 나가는 연결만 쓰고, API는 봇만 부르기 때문.

## 0. 로컬에서 먼저 확인 (Mac)
```bash
# Docker Desktop 설치 후
docker compose up --build        # 포그라운드로 띄워 로그 확인
curl localhost:8000/health
docker compose down
```

## 1. VM 만들기
- AWS EC2 / Lightsail, GCP, Oracle Cloud 무료 티어 등 아무거나. Ubuntu 24.04, 메모리 **2GB 이상**(chromadb + 빌드).
- 보안 그룹(방화벽) 인바운드: `22/tcp` 내 IP만. 8000은 열지 않는다.
- 키 페어(.pem) 받아 두기.

## 2. 접속 + Docker 설치
```bash
ssh -i ~/keys/policy-bot.pem ubuntu@<공개IP>
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && exit     # 다시 접속하면 sudo 없이 docker 사용
```

## 3. 코드와 비밀값 올리기
```bash
git clone https://github.com/seoyoon513/moree-policy-bot.git policy-bot && cd policy-bot
# .env 는 git에 없으므로 내 Mac에서 복사
#   (Mac 터미널) scp -i ~/keys/policy-bot.pem ~/policy-bot/.env ubuntu@<공개IP>:~/policy-bot/.env
chmod 600 .env
mkdir -p data
```
Mac의 기존 인덱스/히스토리를 가져가려면 `scp -r ~/policy-bot/data ubuntu@<공개IP>:~/policy-bot/`.
안 가져가면 4번 후 재인덱싱한다 (Notion이 원본이라 다시 만들 수 있음).

## 4. 실행
```bash
docker compose up -d --build
docker compose ps                 # api 가 (healthy) 인지 확인
docker compose logs -f bot        # "길드 ...에 커맨드 3개 등록" 확인
```
처음 한 번 인덱스 생성:
```bash
curl -X POST localhost:8000/admin/sync-jobs \
  -H "X-Admin-Token: $(grep ^ADMIN_TOKEN .env | cut -d= -f2)" \
  -H "Content-Type: application/json" -d '{}'
```

> ⚠️ **로컬 봇은 꺼 두기.** 같은 토큰으로 Mac과 서버에서 동시에 봇을 띄우면 명령이 두 번 처리된다.

## 5. 업데이트 배포
```bash
cd ~/policy-bot && git pull && docker compose up -d --build
```

## 6. 운영 명령
| 하고 싶은 것 | 명령 |
|---|---|
| 상태 | `docker compose ps` |
| 로그 | `docker compose logs -f --tail=100 api` |
| 컨테이너 안에 들어가기 | `docker compose exec api bash` |
| 평가 돌리기 | `docker compose exec api python -m scripts.eval_rag --sweep` |
| 토큰 사용량 | `curl -s localhost:8000/admin/usage?days=7 -H "X-Admin-Token: ..."` |
| 재시작 | `docker compose restart bot` |
| 전부 내리기 (데이터는 유지) | `docker compose down` |

## 7. 내 Mac에서 서버 API 문서(/docs) 보기
포트를 열지 않고 SSH 터널로:
```bash
ssh -i ~/keys/policy-bot.pem -L 8000:127.0.0.1:8000 ubuntu@<공개IP>
# 브라우저에서 http://localhost:8000/docs
```

## 트러블슈팅
| 증상 | 확인 |
|---|---|
| bot이 계속 재시작 | `docker compose logs bot` — 대부분 `.env` 값 누락 |
| `정책 API 서버(http://api:8000)에 연결할 수 없어요` | `docker compose ps`로 api healthy인지 |
| `Permission denied: /app/data/...` | 호스트 `data/` 소유자가 uid 1000이 아님 → `sudo chown -R 1000:1000 data` |
| 빌드 중 멈춤/Killed | 메모리 부족 → 2GB 이상 VM 또는 swap 추가 |
| Mac(M칩)에서 만든 이미지를 서버로 옮겼더니 `exec format error` | CPU 아키텍처 차이(arm64 vs amd64). 서버에서 직접 빌드하면 해결 |
