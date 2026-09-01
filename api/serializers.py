"""Сборка JSON-ответов для Mini App.

Одно правило важнее остальных: правильный ответ не уходит на клиент раньше, чем
пользователь ответил. В обычной тренировке он раскрывается сразу после проверки
(ТЗ п.4), в полном варианте — только после завершения, потому что до конца варианта
ответ можно менять (ТЗ п.8).
"""
from db import crud
from db.models import (
    KIND_CHOICE,
    KIND_DIGITS,
    KIND_OPEN,
    KIND_MATCH,
    KIND_VARIANT,
    Session,
    SessionItem,
)
from core import profile_meta, scoring
from core.tasks_meta import letter, subtitle, title


def onboarding_payload(user) -> dict:
    """Анкета ученика в виде, готовом к показу: и ключи, и подписи.

    Ключи нужны экрану правки — по ним отмечается выбранное; подписи нужны профилю,
    чтобы не держать второй справочник на клиенте.
    """
    subjects = list(user.subjects or [])
    return {
        "completed": user.onboarded_at is not None,
        "grade": user.grade,
        "math_level": user.math_level,
        "math_title": profile_meta.math_title(user.math_level),
        "subjects": subjects,
        "subject_titles": [profile_meta.subject_title(s) for s in subjects],
        "exams": profile_meta.exam_list(user.math_level, subjects),
        "target_score": user.target_score,
        "target_title": profile_meta.target_title(user.target_score),
    }


def label(index: int) -> str:
    """Подпись варианта. Цифры, а не буквы: условия ЕГЭ говорят
    «запишите номера ответов», и нумерация должна совпадать."""
    return str(index + 1)


def question_payload(item: SessionItem, reveal: bool) -> dict:
    task = item.task
    kind = task.kind or KIND_CHOICE
    correct = list(task.correct or [])
    payload = {
        "position": item.position,
        "task_id": task.id,
        "number": item.task_number,
        "kind": kind,
        "title": title(item.task_number),
        "subtitle": subtitle(item.task_number),
        "text": task.text,
        "passage": task.passage,
        "options": [
            {"index": i, "letter": label(i), "text": text}
            for i, text in enumerate(task.options or [])
        ],
        # Левый столбец задания на соответствие: подписывается буквами А-Д,
        # в отличие от вариантов ответа, которые нумеруются цифрами.
        "match_left": [
            {"index": i, "letter": letter(i), "text": text}
            for i, text in enumerate(task.match_left or [])
        ],
        # Несколько верных ответов бывает только там, где вообще есть выбор.
        "multi": kind == KIND_CHOICE and len(correct) > 1,
        "selected": list(item.selected or []),
        "typed": item.typed or "",
        "answered": item.answered,
    }
    if reveal and item.answered:
        payload["is_correct"] = item.is_correct
        payload["correct"] = correct
        payload["correct_letters"] = ", ".join(label(i) for i in correct)
        payload["answers"] = list(task.answers or [])
    return payload


def _current_position(items: list[SessionItem]) -> int:
    for item in items:
        if not item.answered:
            return item.position
    return items[-1].position if items else 0


def timer_payload(session: Session) -> dict | None:
    if session.kind != KIND_VARIANT or not session.time_limit_sec:
        return None
    return {
        "limit": session.time_limit_sec,
        "spent": crud.elapsed_seconds(session),
        "remaining": crud.remaining_seconds(session),
        "paused": session.resumed_at is None,
    }


def session_payload(session: Session, items: list[SessionItem], position: int | None = None) -> dict:
    """Полное состояние активной сессии — то, что рисует экран решения."""
    answered = sum(1 for item in items if item.answered)
    if position is None:
        position = _current_position(items)
    current = next((i for i in items if i.position == position), None)

    # В варианте правильный ответ не показываем до самого конца.
    reveal = session.kind != KIND_VARIANT

    payload = {
        "id": session.id,
        "kind": session.kind,
        "status": session.status,
        "total": session.total,
        "answered": answered,
        "position": position,
        "task_number": session.task_number,
        "title": title(session.task_number) if session.task_number else "Полный вариант",
        "question": question_payload(current, reveal) if current else None,
        "timer": timer_payload(session),
    }

    if session.kind == KIND_VARIANT:
        payload["nav"] = [
            {"position": i.position, "number": i.task_number, "answered": i.answered}
            for i in items
        ]
    else:
        payload["progress"] = [
            {"position": i.position, "is_correct": i.is_correct} for i in items
        ]
    return payload


def unfinished_payload(session: Session | None, items: list[SessionItem]) -> dict | None:
    """Карточка «Продолжить тренировку» на главной (ТЗ п.7)."""
    if session is None:
        return None
    answered = sum(1 for item in items if item.answered)
    payload = {
        "id": session.id,
        "kind": session.kind,
        "task_number": session.task_number,
        "title": title(session.task_number) if session.task_number else "Полный вариант",
        "answered": answered,
        "total": session.total,
    }
    if session.kind == KIND_VARIANT:
        payload["timer"] = timer_payload(session)
    return payload


def review_payload(items: list[SessionItem]) -> list[dict]:
    """Разбор: задание, ответ пользователя и правильный ответ (ТЗ п.6)."""
    review = []
    for item in items:
        task = item.task
        kind = task.kind or KIND_CHOICE
        correct = list(task.correct or [])
        answers = list(task.answers or [])

        # Как показать правильный ответ — зависит от вида задания.
        if kind in (KIND_OPEN, KIND_DIGITS):
            correct_label = " или ".join(answers)
            yours_label = item.typed or ""
        elif kind == KIND_MATCH:
            correct_label = " ".join(
                f"{letter(i)}-{value}" for i, value in enumerate(correct)
            )
            yours_label = " ".join(
                f"{letter(i)}-{value}" for i, value in enumerate(item.selected or [])
            )
        else:
            correct_label = ", ".join(label(i) for i in correct)
            yours_label = ", ".join(label(i) for i in (item.selected or []))

        review.append({
            "position": item.position,
            "number": item.task_number,
            "kind": kind,
            "title": title(item.task_number),
            "text": task.text,
            "passage": task.passage,
            "options": [
                {"index": i, "letter": label(i), "text": text}
                for i, text in enumerate(task.options or [])
            ],
            "match_left": [
                {"index": i, "letter": letter(i), "text": text}
                for i, text in enumerate(task.match_left or [])
            ],
            "selected": list(item.selected or []),
            "typed": item.typed or "",
            "correct": correct,
            "answers": answers,
            "correct_letters": correct_label,
            "yours_label": yours_label,
            "is_correct": item.is_correct,
            "answered": item.answered,
        })
    return review


def result_payload(session: Session, items: list[SessionItem]) -> dict:
    """Итог тренировки (ТЗ п.6) или полного варианта (ТЗ п.10)."""
    correct = session.correct_count or 0
    wrong = session.wrong_count or 0
    skipped = session.skipped_count or 0
    graded = correct + wrong

    payload = {
        "id": session.id,
        "kind": session.kind,
        "total": session.total,
        "correct": correct,
        "wrong": wrong,
        "skipped": skipped,
        "accuracy": scoring.accuracy(correct, graded),
        "task_number": session.task_number,
        "title": title(session.task_number) if session.task_number else "Полный вариант",
        "review": review_payload(items),
    }

    if session.kind == KIND_VARIANT:
        payload["raw_score"] = session.raw_score or 0
        payload["max_raw_score"] = scoring.MAX_RAW_SCORE
        payload["time_spent"] = session.time_spent_sec or 0
        payload["time_limit"] = session.time_limit_sec
    return payload


def history_payload(sessions: list[Session]) -> list[dict]:
    """История полных вариантов (ТЗ п.11)."""
    return [
        {
            "id": s.id,
            "variant_id": s.variant_id,
            "finished_at": s.finished_at.isoformat() if s.finished_at else None,
            "time_spent": s.time_spent_sec or 0,
            "correct": s.correct_count or 0,
            "wrong": s.wrong_count or 0,
            "skipped": s.skipped_count or 0,
            "raw_score": s.raw_score or 0,
            "max_raw_score": scoring.MAX_RAW_SCORE,
        }
        for s in sessions
    ]
