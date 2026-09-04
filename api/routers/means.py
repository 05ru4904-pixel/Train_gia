"""Средства выразительности: тренажёр к заданию №22.

Отдельный раздел со своим источником вопросов и своей таблицей. Механика не та,
что у карточек: программа проверяет ответ сама, поэтому «Знаю / Не знаю» здесь
нет, как нет ни таймеров, ни слабых, ни выученных.

* **Подход** — десять выражений подряд, у каждого четыре кнопки: правильный приём
  и три случайные обманки из перечня. Вопросы каждый раз новые и случайные.
* **Проверка — на сервере.** Правильный приём не уходит на клиент, пока ученик не
  ответил (правило проекта): ответ отсылается и возвращается с вердиктом.
* **Копится только точность по трём группам** приёмов. На подбор вопросов она не
  влияет и в общую статистику не входит — та про решённые задания.

Подход нигде не хранится: вопрос адресуется парой «задание + позиция», и этого
хватает, чтобы проверить ответ. Выход из приложения на середине ничего не теряет
— всё, что отвечено, уже записано.
"""
import random

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.deps import CurrentUser, DbSession
from core import means
from core.scoring import accuracy
from db import crud

router = APIRouter(tags=["means"])


class MeansAnswer(BaseModel):
    task_id: str = Field(min_length=1, max_length=12)
    position: int = Field(ge=0, le=99)
    term: str = Field(min_length=1, max_length=64)


def _groups_payload(stats: dict[str, tuple[int, int]]) -> list[dict]:
    """Точность по трём группам — то, что показывается в самом тренажёре."""
    rows = []
    for group_id in means.GROUP_ORDER:
        total, correct = stats.get(group_id, (0, 0))
        rows.append({
            "id": group_id,
            "title": means.GROUP_TITLES[group_id],
            "total": total,
            "correct": correct,
            "accuracy": accuracy(correct, total),
        })
    return rows


def _state_payload(stats: dict[str, tuple[int, int]], available: int) -> dict:
    total = sum(pair[0] for pair in stats.values())
    correct = sum(pair[1] for pair in stats.values())
    return {
        **means.deck_payload(),
        # Сколько вопросов вообще можно задать: пять позиций с каждого залитого
        # задания №22. Ноль значит, что вариантов в базе ещё нет.
        "available": available,
        "answered": total,
        "correct": correct,
        "accuracy": accuracy(correct, total),
        "groups": _groups_payload(stats),
    }


async def _available(db) -> int:
    tasks = await crud.means_source_tasks(db, means.TASK_NUMBER)
    return len(means.pairs_from_tasks(tasks))


@router.get("/means")
async def state(user: CurrentUser, db: DbSession) -> dict:
    """Экран перед подходом: накопленная точность и есть ли из чего спрашивать."""
    stats = await crud.means_stats(db, user.id)
    return _state_payload(stats, await _available(db))


@router.get("/means/session")
async def session(user: CurrentUser, db: DbSession) -> dict:
    """Набирает подход: до десяти случайных вопросов, в подходе не повторяются."""
    tasks = await crud.means_source_tasks(db, means.TASK_NUMBER)
    pairs = means.pairs_from_tasks(tasks)
    random.shuffle(pairs)
    chosen = pairs[: means.SESSION_SIZE]

    stats = await crud.means_stats(db, user.id)
    return {
        "questions": [means.question_payload(pair) for pair in chosen],
        **_state_payload(stats, len(pairs)),
    }


@router.post("/means/answer")
async def answer(payload: MeansAnswer, user: CurrentUser, db: DbSession) -> dict:
    """Проверяет ответ и засчитывает его в точность группы.

    Правильный приём берётся из задания заново, а не из того, что прислал клиент:
    подменённый запрос не должен уметь записать себе верный ответ.
    """
    chosen = means.normalize(payload.term)
    if chosen not in means.TERMS:
        raise HTTPException(400, {
            "code": "unknown_term",
            "message": "Такого средства выразительности нет в перечне",
        })

    task = await crud.get_task(db, payload.task_id)
    if task is None or task.number != means.TASK_NUMBER:
        raise HTTPException(404, {"code": "no_such_task", "message": "Задание не найдено"})

    correct_term = means.term_at(task, payload.position)
    if correct_term is None:
        raise HTTPException(404, {"code": "no_such_question", "message": "Вопрос не найден"})

    is_correct = chosen == correct_term
    # Точность считается по группе правильного приёма, а не выбранного: столбец
    # «Лексические» — это доля верных среди вопросов, где ответом был троп.
    await crud.bump_means(db, user.id, means.TERMS[correct_term], is_correct)

    stats = await crud.means_stats(db, user.id)
    return {
        "ok": True,
        "is_correct": is_correct,
        "correct": correct_term,
        "group": means.TERMS[correct_term],
        "group_title": means.GROUP_TITLES[means.TERMS[correct_term]],
        "groups": _groups_payload(stats),
    }


@router.post("/means/reset")
async def reset(user: CurrentUser, db: DbSession) -> dict:
    """Стирает накопленную точность. Вопросы не помечаются решёнными нигде, так
    что сбрасывать больше нечего."""
    forgotten = await crud.reset_means(db, user.id)
    return {"ok": True, "forgotten": forgotten, **_state_payload({}, await _available(db))}
