from __future__ import annotations

import json
import urllib.error
import urllib.request

UA = "TechDiag/1.0 (+diagnostico tecnico)"


class HttpError(RuntimeError):
    def __init__(self, msg: str, status: int | None = None):
        super().__init__(msg)
        self.status = status


def get_json(url: str, headers: dict | None = None, timeout: int = 15):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise HttpError(f"HTTP {e.code}", e.code) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise HttpError(f"Falha de conexão: {e}") from e
    except json.JSONDecodeError as e:
        raise HttpError("Resposta inválida do servidor") from e
