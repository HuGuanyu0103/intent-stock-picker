"""扶摇金融数据客户端：X-api-key 鉴权、5 req/s 限速、指数退避重试、JSON 缓存。"""
import os
import time
import threading
import requests

BASE = "https://fuyao.aicubes.cn/api"
MIN_INTERVAL = 0.22  # 5 req/s 留余量
_max_retries = 3


class RateLimiter:
    def __init__(self):
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self):
        with self._lock:
            dt = time.time() - self._last
            if dt < MIN_INTERVAL:
                time.sleep(MIN_INTERVAL - dt)
            self._last = time.time()


_limiter = RateLimiter()


def _key():
    k = os.environ.get("FUYAO_API_KEY")
    if not k:
        raise RuntimeError("FUYAO_API_KEY 未配置")
    return k


def get(path, params=None, timeout=15):
    url = f"{BASE}{path}"
    last_err = None
    for attempt in range(_max_retries):
        _limiter.wait()
        try:
            r = requests.get(url, params=params, headers={"X-api-key": _key()}, timeout=timeout)
            j = r.json()
            if j.get("code") == 0:
                return j["data"]
            last_err = RuntimeError(f"code={j.get('code')} {j.get('message')}")
        except Exception as e:  # 网络错误/超时/非 JSON
            last_err = e
        time.sleep(0.5 * (2 ** attempt))
    raise last_err
