"""Сквозной тест API: тренировка, полный вариант, статистика.

Гоняется на SQLite, чтобы не требовать поднятого Postgres. Все запросы идут на
ОДНОМ event loop через httpx: TestClient создаёт новый loop на каждый запрос, а
соединения в пуле привязаны к тому loop, где создались (playbook 4.1).

Запуск: python tests/test_api.py
"""
import asyncio
import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TOKEN = "123456:TEST-TOKEN-abcdefghijklmnop"
DB_FILE = Path(tempfile.gettempdir()) / f"train_gia_test_{os.getpid()}.sqlite3"

# Окружение обязано быть готово до импорта config — настройки читаются при импорте.
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB_FILE.as_posix()}"
os.environ["BOT_TOKEN"] = TOKEN
os.environ["ADMIN_BOT_TOKEN"] = ""
os.environ["WEBAPP_URL"] = "https://example.test/app"

from httpx import ASGITransport, AsyncClient  # noqa: E402

from api.main import app  # noqa: E402
from core import scoring  # noqa: E402
from core.parser import ParsedTask  # noqa: E402
from core.tasks_meta import TASK_NUMBERS  # noqa: E402
from db import crud  # noqa: E402
from db.database import SessionMaker, engine, init_db  # noqa: E402
from db.models import Base  # noqa: E402

USER = {"id": 4242, "first_name": "Заур", "username": "zaur"}


def init_data(user=None) -> str:
    payload = {
        "user": json.dumps(user or USER, ensure_ascii=False, separators=(",", ":")),
        "auth_date": str(int(time.time())),
    }
    check = "\n".join(f"{k}={payload[k]}" for k in sorted(payload))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    payload["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(payload)


def make_task(number: int, index: int) -> ParsedTask:
    """Задание с предсказуемым правильным ответом: индекс правильного = index % 4."""
    correct = index % 4
    return ParsedTask(
        number=number,
        text=f"Вопрос {index} по заданию {number}?",
        options=[f"вариант {i}" for i in range(4)],
        correct=[correct],
    )


async def seed(per_number: int = 4) -> None:
    async with SessionMaker() as db:
        for number in TASK_NUMBERS:
            for index in range(per_number):
                await crud.create_task(db, make_task(number, index))


class Client:
    """Обёртка над httpx: подставляет подпись и разворачивает ошибки."""

    def __init__(self, http: AsyncClient, user=None):
        self.http = http
        self.headers = {"X-Telegram-Init-Data": init_data(user)}

    async def get(self, path, expect=200):
        return await self._call("GET", path, None, expect)

    async def post(self, path, body=None, expect=200):
        return await self._call("POST", path, body, expect)

    async def _call(self, method, path, body, expect):
        response = await self.http.request(method, path, json=body, headers=self.headers)
        assert response.status_code == expect, (
            f"{method} {path} -> {response.status_code}, ожидался {expect}: {response.text[:300]}"
        )
        return response.json() if response.content else None


async def answer_all(client, correct_ratio=1.0, already_correct=0):
    """Отвечает на оставшиеся вопросы тренировки. correct_ratio — доля верных."""
    result = None
    correct_given = already_correct

    while True:
        response = await client.http.request("GET", "/api/session", headers=client.headers)
        if response.status_code == 404:
            break  # активной сессии больше нет — тренировка уже завершилась
        assert response.status_code == 200, response.text[:300]
        state = response.json()
        if state["finished"]:
            result = state["result"]
            break

        session = state["session"]
        question = session["question"]
        target = round(session["total"] * correct_ratio)

        async with SessionMaker() as db:
            task = await crud.get_task(db, question["task_id"])
            correct_index = list(task.correct)[0]

        if correct_given < target:
            selected = [correct_index]
            correct_given += 1
        else:
            selected = [(correct_index + 1) % len(question["options"])]

        data = await client.post(
            "/api/session/answer",
            {"position": question["position"], "selected": selected},
        )
        if data.get("finished"):
            result = data["result"]
            break
    return result


# --------------------------------------------------------------------------- #
# Сценарии
# --------------------------------------------------------------------------- #
async def scenario_state_and_auth(client, http):
    unauthorized = await http.get("/api/state")
    assert unauthorized.status_code == 401, "запрос без подписи обязан отклоняться"

    state = await client.get("/api/state")
    assert state["user"]["id"] == USER["id"]
    assert state["user"]["name"] == "Заур"
    assert state["unfinished"] is None
    assert state["tasks_total"] == 26 * 4
    assert state["training_counts"] == [6, 9, 12, 15]
    assert state["counts"]["4"] == 4

    tasks = await client.get("/api/tasks")
    assert len(tasks["tasks"]) == 26
    assert tasks["tasks"][3]["number"] == 4
    assert tasks["tasks"][3]["title"] == "Орфоэпические нормы"
    print("  ok  состояние, авторизация и список заданий")


async def scenario_not_enough_tasks(client):
    """В базе по 4 задания каждого номера — на 6 вопросов их не хватит (ТЗ п.4)."""
    response = await client.post("/api/training/start", {"number": 4, "count": 6}, expect=409)
    assert response["detail"]["code"] == "not_enough_tasks"
    assert response["detail"]["available"] == 4
    print("  ok  нехватка заданий сообщается явно")


async def scenario_training(client):
    async with SessionMaker() as db:
        for index in range(4, 20):
            await crud.create_task(db, make_task(4, index))

    session = await client.post("/api/training/start", {"number": 4, "count": 12})
    assert session["total"] == 12
    assert session["task_number"] == 4
    assert session["question"]["position"] == 0
    assert "correct" not in session["question"], "правильный ответ не должен приходить заранее"

    task_ids = set()
    state = await client.get("/api/state")
    assert state["unfinished"]["answered"] == 0
    assert state["unfinished"]["total"] == 12

    # первый ответ — заведомо верный
    question = session["question"]
    async with SessionMaker() as db:
        task = await crud.get_task(db, question["task_id"])
        correct = list(task.correct)
    first = await client.post(
        "/api/session/answer", {"position": 0, "selected": correct}
    )
    assert first["is_correct"] is True
    assert first["correct"] == correct

    # повторный ответ на то же задание запрещён (ТЗ п.4)
    await client.post("/api/session/answer", {"position": 0, "selected": correct}, expect=409)

    # прогресс переживает «выход»: следующий вопрос отдаётся с того же места
    state = await client.get("/api/session")
    assert state["session"]["question"]["position"] == 1
    assert state["session"]["answered"] == 1

    result = await answer_all(client, correct_ratio=0.75, already_correct=1)
    assert result is not None, "после последнего ответа должен прийти результат"
    assert result["total"] == 12
    assert result["correct"] + result["wrong"] == 12
    assert result["accuracy"] == scoring.accuracy(result["correct"], 12)
    assert len(result["review"]) == 12
    for item in result["review"]:
        task_ids.add(item["position"])
    assert len(task_ids) == 12, "вопросы в тренировке не должны повторяться"

    after = await client.get("/api/state")
    assert after["unfinished"] is None, "завершённая тренировка не предлагается к продолжению"
    print(f"  ok  тренировка целиком: {result['correct']} верных из 12, {result['accuracy']}%")
    return result


async def scenario_unique_questions(client):
    session = await client.post("/api/training/start", {"number": 4, "count": 15})
    state = await client.get("/api/session")
    positions = [cell["position"] for cell in state["session"]["progress"]]
    assert len(set(positions)) == 15

    ids = []
    for position in range(15):
        page = await client.get(f"/api/session?position={position}")
        ids.append(page["session"]["question"]["task_id"])
    assert len(set(ids)) == 15, "в одной тренировке задания не должны повторяться"
    print("  ok  задания внутри тренировки не повторяются")


async def scenario_abandoned_not_counted(client):
    """Брошенная тренировка не попадает в статистику (ТЗ п.7 и п.12)."""
    async with SessionMaker() as db:
        for number in (5, 6):
            for index in range(4, 10):
                await crud.create_task(db, make_task(number, index))

    before = await client.get("/api/stats")
    solved_before = before["overall"]["total"]

    session = await client.post("/api/training/start", {"number": 5, "count": 6})
    question = session["question"]
    async with SessionMaker() as db:
        task = await crud.get_task(db, question["task_id"])
        correct = list(task.correct)
    await client.post("/api/session/answer", {"position": 0, "selected": correct})

    # новая тренировка сбрасывает предыдущую
    await client.post("/api/training/start", {"number": 6, "count": 6})

    after = await client.get("/api/stats")
    assert after["overall"]["total"] == solved_before, (
        "ответы из сброшенной тренировки не должны учитываться"
    )
    print("  ok  ответы брошенной тренировки в статистику не попали")


async def scenario_variant(client):
    session = await client.post("/api/variant/start")
    assert session["kind"] == "variant"
    assert session["total"] == 26
    assert len(session["nav"]) == 26
    assert session["timer"]["limit"] == scoring.VARIANT_TIME_LIMIT_SEC
    assert session["timer"]["paused"] is False
    assert "correct" not in session["question"], (
        "в варианте правильный ответ нельзя раскрывать до завершения"
    )

    # отвечаем на 20 из 26, из них 14 верно; порядок произвольный (ТЗ п.8)
    expected_raw = 0
    expected_correct = 0
    for position in [5, 0, 1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]:
        page = await client.get(f"/api/session?position={position}")
        question = page["session"]["question"]
        async with SessionMaker() as db:
            task = await crud.get_task(db, question["task_id"])
            correct = list(task.correct)
        answer_right = expected_correct < 14
        selected = correct if answer_right else [(correct[0] + 1) % 4]
        await client.post(
            "/api/session/answer", {"position": position, "selected": selected}
        )
        if answer_right:
            expected_correct += 1
            expected_raw += scoring.max_points(question["number"])

    # ответ можно изменить, пока вариант не завершён
    page = await client.get("/api/session?position=0")
    assert page["session"]["question"]["answered"] is True
    await client.post("/api/session/answer", {"position": 0, "selected": [0]}, expect=200)

    # пауза и снятие с паузы (ТЗ п.9)
    paused = await client.post("/api/variant/pause")
    assert paused["timer"]["paused"] is True
    frozen = paused["timer"]["remaining"]
    resumed = await client.post("/api/variant/resume")
    assert resumed["timer"]["paused"] is False
    assert abs(resumed["timer"]["remaining"] - frozen) <= 2

    finished = await client.post("/api/session/finish")
    result = finished["result"]
    assert result["kind"] == "variant"
    assert result["correct"] + result["wrong"] == 20
    assert result["skipped"] == 6
    assert result["max_raw_score"] == scoring.MAX_RAW_SCORE
    assert result["test_score"] == scoring.test_score(result["raw_score"])
    assert result["test_score_is_approximate"] is True
    assert len(result["review"]) == 26
    revealed = [item for item in result["review"] if item["correct"]]
    assert len(revealed) == 26, "после завершения разбор доступен по всем заданиям"
    print(
        f"  ok  полный вариант: {result['correct']} верных, {result['skipped']} пропущено, "
        f"первичный {result['raw_score']}/{result['max_raw_score']}, тестовый {result['test_score']}"
    )
    return result


async def scenario_stats(client, variant_result):
    stats = await client.get("/api/stats")
    overall = stats["overall"]
    assert overall["total"] > 0
    assert overall["correct"] + overall["wrong"] == overall["total"]
    assert overall["accuracy"] == scoring.accuracy(overall["correct"], overall["total"])

    by_number = {row["number"]: row for row in stats["tasks"]}
    assert len(by_number) == 26
    assert by_number[4]["title"] == "Орфоэпические нормы"
    assert by_number[4]["total"] >= 12

    assert len(stats["variants"]) == 1
    history = stats["variants"][0]
    assert history["raw_score"] == variant_result["raw_score"]
    assert history["test_score"] == variant_result["test_score"]
    assert history["correct"] == variant_result["correct"]

    empty = await client.get("/api/stats?date_from=2000-01-01&date_to=2000-12-31")
    assert empty["overall"]["total"] == 0, "фильтр по датам должен отсекать всё лишнее"
    assert empty["variants"] == []

    await client.get("/api/stats?date_from=не-дата", expect=400)
    await client.get("/api/stats?date_from=2026-05-01&date_to=2026-01-01", expect=400)
    print(f"  ok  статистика: решено {overall['total']}, точность {overall['accuracy']}%")


async def scenario_profile(client):
    profile = await client.get("/api/profile")
    assert profile["name"] == "Заур"
    assert profile["username"] == "zaur"
    assert profile["plan"] == "free"
    assert profile["is_pro"] is False
    assert profile["plan_until"] is None
    assert profile["registered_at"]
    assert profile["solved_total"] > 0
    print("  ok  профиль")


async def scenario_isolation(http):
    """Второй пользователь не видит чужих данных."""
    other = Client(http, user={"id": 9999, "first_name": "Гость"})
    stats = await other.get("/api/stats")
    assert stats["overall"]["total"] == 0
    state = await other.get("/api/state")
    assert state["unfinished"] is None
    assert state["user"]["id"] == 9999
    print("  ok  данные пользователей изолированы")


async def scenario_mini_app_page(http):
    page = await http.get("/app")
    assert page.status_code == 200
    body = page.text
    assert "no-store" in page.headers.get("cache-control", "")
    assert "{{" not in body, "в отданной странице не должно остаться шаблонных вставок"
    assert "/static/app.js?v=" in body, "ссылки на статику обязаны версионироваться"
    assert "/static/app.css?v=" in body
    assert 'src="./' not in body and 'href="./' not in body, "относительных путей быть не должно"
    print("  ok  страница Mini App отдаётся с версионированной статикой")


async def main() -> int:
    if DB_FILE.exists():
        DB_FILE.unlink()
    await init_db()

    failures = []
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        client = Client(http)
        try:
            await seed()
            await scenario_state_and_auth(client, http)
            await scenario_not_enough_tasks(client)
            await scenario_training(client)
            await scenario_unique_questions(client)
            await scenario_abandoned_not_counted(client)
            variant_result = await scenario_variant(client)
            await scenario_stats(client, variant_result)
            await scenario_profile(client)
            await scenario_isolation(http)
            await scenario_mini_app_page(http)
        except AssertionError as exc:
            failures.append(str(exc))
            import traceback
            traceback.print_exc()

    await engine.dispose()
    if DB_FILE.exists():
        DB_FILE.unlink()

    print("--- провалено:", len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
