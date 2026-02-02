import hmac
import hashlib
from urllib.parse import parse_qsl
from typing import Dict, Any

def verify_telegram_webapp_init_data(init_data: str, bot_token: str) -> Dict[str, Any]:
    if not init_data:
        raise ValueError("Empty init_data")

    data = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = data.pop("hash", None)
    if not received_hash:
        raise ValueError("No hash in init_data")

    pairs = sorted((k, v) for k, v in data.items())
    data_check_string = "\n".join([f"{k}={v}" for k, v in pairs])

    secret_key = hmac.new(
        key=b"WebAppData",
        msg=bot_token.encode("utf-8"),
        digestmod=hashlib.sha256
    ).digest()

    calculated_hash = hmac.new(
        key=secret_key,
        msg=data_check_string.encode("utf-8"),
        digestmod=hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        raise ValueError("Invalid init_data hash")

    import json
    if "user" in data:
        data["user"] = json.loads(data["user"])

    return data
