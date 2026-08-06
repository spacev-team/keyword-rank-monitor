"""Google Chat 알림 — 런 요약 + 순위 변화 알림(웹훅 미설정 시 조용히 생략).

external-metrics-monitor notify.py 의 재시도 정책(일시 오류 지수 백오프)을 축약 이식.
"""
from __future__ import annotations

import time

import requests

_POST_RETRIES = 3
_RETRY_STATUS = {429, 500, 502, 503, 504}


def _post(webhook_url: str, payload: dict) -> bool:
    for attempt in range(_POST_RETRIES):
        try:
            r = requests.post(webhook_url, json=payload, timeout=15)
            if r.status_code < 300:
                return True
            if r.status_code not in _RETRY_STATUS:
                return False  # 4xx = 재시도 무의미
        except requests.RequestException:
            pass
        time.sleep(2 * 2 ** attempt)
    return False


def google_chat(webhook_url: str | None, text: str) -> None:
    if webhook_url and text.strip():
        # Chat 평문 한도(~4096자) 보수 분할
        for p in range(0, len(text), 3500):
            _post(webhook_url, {"text": text[p:p + 3500]})
