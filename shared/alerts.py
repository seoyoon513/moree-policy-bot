"""운영 채널 알림 (디스코드 웹훅). 봇(시작 알림)과 API(동기화 실패·후속 작업 포기·LLM 설정 문제)가 같이 쓴다."""
from __future__ import annotations

import json
import logging
import urllib.request

from shared.config import Settings

log = logging.getLogger("policy-ops")


def notify(s: Settings, message: str) -> bool:
    """운영 알림. 보냈으면 True. 블로킹 호출이라 asyncio 코드에서는 asyncio.to_thread로 부른다.
    웹훅이 없거나 보내기에 실패해도 예외를 내지 않는다 (알림 실패가 본 기능을 막으면 안 된다)."""
    log.warning("운영 알림: %s", message)
    url = s.resolved_ops_webhook_url()
    if not url:
        return False
    try:
        req = urllib.request.Request(url, method="POST", data=json.dumps({"content": message[:1900]}).encode(),
                                     headers={"Content-Type": "application/json", "User-Agent": "policy-bot"})
        urllib.request.urlopen(req, timeout=5).close()
        return True
    except OSError as e:
        log.warning("운영 알림 전송 실패: %s", e)
        return False
