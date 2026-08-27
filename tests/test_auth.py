"""Тесты проверки подписи initData. Запуск: python tests/test_auth.py"""
import hashlib
import hmac
import json
import sys
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.auth import MAX_AUTH_AGE_SEC, parse_user, verify_init_data  # noqa: E402

TOKEN = "123456:TEST-TOKEN-abcdefghijklmnop"
USER = {"id": 42, "first_name": "Заур", "username": "zaur"}


def make_init_data(token=TOKEN, auth_date=None, **extra) -> str:
    pairs = {
        "user": json.dumps(USER, ensure_ascii=False, separators=(",", ":")),
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAE1",
        **extra,
    }
    check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


def _expect_error(init_data: str, fragment: str, token: str = TOKEN):
    try:
        verify_init_data(init_data, token)
    except ValueError as exc:
        assert fragment in str(exc), f"ожидал «{fragment}», получил «{exc}»"
    else:
        raise AssertionError(f"ожидалась ValueError, фрагмент: {fragment}")


def test_valid_init_data():
    pairs = verify_init_data(make_init_data(), TOKEN)
    user = parse_user(pairs)
    assert user.id == 42
    assert user.first_name == "Заур"
    assert user.username == "zaur"
    assert user.last_name is None


def test_wrong_token_rejected():
    _expect_error(make_init_data(), "подпись", token="999:OTHER-TOKEN")


def test_tampered_user_rejected():
    """Главное, ради чего всё это: подменённый id не должен проходить."""
    pairs = dict(parse_qsl(make_init_data(), keep_blank_values=True))
    user = json.loads(pairs["user"])
    user["id"] = 777
    pairs["user"] = json.dumps(user, ensure_ascii=False, separators=(",", ":"))
    hacked = urlencode(pairs)

    # убеждаемся, что подмена действительно доехала до строки
    assert json.loads(dict(parse_qsl(hacked))["user"])["id"] == 777
    _expect_error(hacked, "подпись")


def test_missing_hash():
    _expect_error("user=%7B%7D&auth_date=1", "нет hash")


def test_empty_init_data():
    _expect_error("", "пустая")


def test_stale_init_data_rejected():
    old = int(time.time()) - MAX_AUTH_AGE_SEC - 60
    _expect_error(make_init_data(auth_date=old), "устарела")


def test_fresh_enough_init_data_accepted():
    recent = int(time.time()) - MAX_AUTH_AGE_SEC + 600
    verify_init_data(make_init_data(auth_date=recent), TOKEN)


def test_extra_fields_are_covered_by_signature():
    """Новые поля Telegram не должны ломать проверку."""
    pairs = verify_init_data(make_init_data(chat_type="private", start_param="ref7"), TOKEN)
    assert pairs["start_param"] == "ref7"


def test_parse_user_errors():
    for pairs, fragment in (
        ({}, "нет данных пользователя"),
        ({"user": "не json"}, "повреждены"),
        ({"user": '{"first_name":"X"}'}, "нет id"),
    ):
        try:
            parse_user(pairs)
        except ValueError as exc:
            assert fragment in str(exc), f"ожидал «{fragment}», получил «{exc}»"
        else:
            raise AssertionError(f"ожидалась ValueError: {fragment}")


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok  {name}")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"FAIL  {name}: {exc}")
    print("--- провалено:", failed)
    sys.exit(1 if failed else 0)
