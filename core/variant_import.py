"""Проверка варианта и заливка его в базу.

Логика вынесена из `scripts/import_variant.py`, чтобы её мог звать и админ-бот:
функции ничего не печатают, а возвращают отчёт. Печатает или шлёт в Telegram уже
вызывающая сторона.

Схема входа — то, что отдаёт `core.raw_variant.Result.payload()`:

    {"texts": {"t1": "..."}, "tasks": [{"number": 1, "kind": "open", ...}]}
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

from core.parser import ParsedTask
from core.tasks_meta import (
    KIND_CHOICE,
    KIND_DIGITS,
    KIND_MATCH,
    KIND_OPEN,
    LAST_TASK,
    TASK_KINDS,
)
from db import crud
from db.database import SessionMaker


@dataclass
class ImportTask:
    """Задание, готовое к заливке. Держит все четыре вида сразу."""

    number: int
    kind: str
    text: str
    passage: str | None
    options: list[str]
    match_left: list[str]
    correct: list[int]
    answers: list[str]
    # Постоянный номер задания у источника. Есть у всего, что пришло через /upload;
    # у заданий из ручного шаблона его нет.
    source_id: int | None = None


@dataclass
class Loaded:
    tasks: list[ImportTask] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and bool(self.tasks)


@dataclass
class ImportReport:
    created: list[tuple[int, str]] = field(default_factory=list)
    # Задания, которые уже лежали в базе: (номер, ID существующего). Заливка
    # переиспользует их, а админ-бот показывает по ID карточку — чтобы было видно,
    # с чем именно совпало.
    reused: list[tuple[int, str]] = field(default_factory=list)
    duplicates: int = 0
    total_in_db: int = 0
    variant_number: int | None = None
    variant_status: str | None = None   # «собран», «обновлён» или None
    # Номер варианта, который уже собран из этого же №26. Заполнен — значит заливка
    # не состоялась: такой вариант в базе уже есть.
    duplicate_of: int | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def is_duplicate(self) -> bool:
        return self.duplicate_of is not None


def _as_list(value) -> list:
    return list(value) if isinstance(value, list) else []


def _as_text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def load_payload(data, where: str = "вариант") -> Loaded:
    """Проверяет структуру варианта и собирает задания к заливке."""
    result = Loaded()

    if not isinstance(data, dict) or "tasks" not in data:
        result.errors.append(f"{where}: нет ключа «tasks» на верхнем уровне")
        return result

    texts = data.get("texts") or {}
    if not isinstance(texts, dict):
        result.errors.append(f"{where}: «texts» должен быть объектом")
        texts = {}

    seen: set[int] = set()
    for position, raw in enumerate(data["tasks"], start=1):
        place = f"{where}, задание на позиции {position}"
        if not isinstance(raw, dict):
            result.errors.append(f"{place}: не объект")
            continue

        number = raw.get("number")
        if not isinstance(number, int) or not 1 <= number <= LAST_TASK:
            result.errors.append(f"{place}: номер {number!r} вне диапазона 1-{LAST_TASK}")
            continue
        place = f"{where}, №{number}"
        if number in seen:
            result.errors.append(f"{place}: номер встречается второй раз")
            continue
        seen.add(number)

        kind = raw.get("kind")
        if kind not in TASK_KINDS:
            result.errors.append(
                f"{place}: неизвестный вид {kind!r}, ожидался один из {TASK_KINDS}"
            )
            continue

        text = _as_text(raw.get("text"))
        if not text:
            result.errors.append(f"{place}: пустое условие")
            continue

        source_id = raw.get("source_id")
        if source_id is not None and not isinstance(source_id, int):
            result.errors.append(f"{place}: source_id={source_id!r}, ожидалось целое число")
            continue

        options = [_as_text(o) for o in _as_list(raw.get("options"))]
        match_left = [_as_text(o) for o in _as_list(raw.get("match_left"))]
        correct = [c for c in _as_list(raw.get("correct")) if isinstance(c, int)]
        answers = [_as_text(a) for a in _as_list(raw.get("answers")) if _as_text(a)]

        # Исходный текст: либо общий по ссылке, либо свой у задания.
        ref = raw.get("text_ref")
        passage_parts = []
        if ref:
            if ref not in texts:
                result.errors.append(f"{place}: text_ref={ref!r}, но такого текста нет в «texts»")
                continue
            passage_parts.append(_as_text(texts[ref]))
        material = _as_text(raw.get("material"))
        if material:
            passage_parts.append(material)
        passage = "\n\n".join(p for p in passage_parts if p) or None

        problem = validate(kind, options, match_left, correct, answers)
        if problem:
            result.errors.append(f"{place}: {problem}")
            continue

        result.tasks.append(
            ImportTask(
                number=number,
                source_id=source_id,
                kind=kind,
                text=text,
                passage=passage,
                options=options,
                match_left=match_left,
                correct=correct,
                answers=answers,
            )
        )
    return result


def load_file(path: Path) -> Loaded:
    """Читает и проверяет один JSON-файл варианта."""
    result = Loaded()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        result.errors.append(f"{path.name}: файл не разбирается как JSON — {exc}")
        return result
    except UnicodeDecodeError:
        result.errors.append(f"{path.name}: файл не в кодировке UTF-8")
        return result
    return load_payload(data, where=path.name)


def validate(kind, options, match_left, correct, answers) -> str | None:
    """Проверяет, что для своего вида задание заполнено полностью и непротиворечиво."""
    if kind == KIND_CHOICE:
        if len(options) < 2:
            return "меньше двух вариантов ответа"
        if not correct:
            return "не указан правильный ответ"
        bad = [c for c in correct if not 1 <= c <= len(options)]
        if bad:
            return f"в correct есть {bad}, а вариантов всего {len(options)}"
    elif kind == KIND_MATCH:
        if not match_left:
            return "пустой левый столбец (match_left)"
        if len(options) < 2:
            return "пустой правый столбец (options)"
        if len(correct) != len(match_left):
            return f"в correct {len(correct)} значений, а слева {len(match_left)} позиций"
        bad = [c for c in correct if not 1 <= c <= len(options)]
        if bad:
            return f"в correct есть {bad}, а справа всего {len(options)} позиций"
    elif kind in (KIND_OPEN, KIND_DIGITS):
        if not answers:
            return "не перечислены допустимые ответы (answers)"
        if kind == KIND_DIGITS and not all(any(ch.isdigit() for ch in a) for a in answers):
            return f"вид digits, но в answers нет цифр: {answers}"
    return None


def to_parsed(task: ImportTask) -> ParsedTask:
    """Приводит к структуре, которую понимает слой БД.

    В базе индексы вариантов нумеруются с нуля, а источник — с единицы. У задания на
    соответствие correct — это позиции правого столбца, они остаются с единицы.
    """
    return ParsedTask(
        number=task.number,
        text=task.text,
        options=task.options,
        correct=[c - 1 for c in task.correct] if task.kind == KIND_CHOICE else list(task.correct),
        kind=task.kind,
        answers=list(task.answers),
        match_left=list(task.match_left),
        passage=task.passage,
        source_id=task.source_id,
    )


def _squeeze(value) -> str:
    return " ".join(str(value or "").lower().split())


def make_fingerprint(number, kind, text, passage, options, match_left, answers) -> str:
    """Отпечаток по содержимому — защита от повторной заливки без ID источника.

    Материал (`passage`) обязателен в отпечатке: у видов open и digits условие
    типовое и повторяется из варианта в вариант («Укажите цифру(-ы), на месте
    которой(-ых) пишется НН»), а различаются задания только материалом. Без него
    восемь заданий второго варианта были приняты за уже залитые и потерялись.
    """
    parts = [str(number), kind or "", _squeeze(text), _squeeze(passage)]
    parts += [_squeeze(o) for o in options or []]
    parts += [_squeeze(o) for o in match_left or []]
    parts += [_squeeze(a) for a in answers or []]
    return "|".join(parts)


def fingerprint(task: ImportTask) -> str:
    return make_fingerprint(
        task.number, task.kind, task.text, task.passage,
        task.options, task.match_left, task.answers,
    )


async def import_tasks(tasks: list[ImportTask], variant_number: int | None = None) -> ImportReport:
    """Заливает задания и, если попросили, собирает из них вариант.

    Дубли ищутся по ID источника — постоянному номеру задания на сайте. Он не
    меняется от правки текста, поэтому надёжнее отпечатка содержимого. Проверок
    две, и они про разное:

      * **вариант целиком** опознаётся по последнему заданию (№26). Совпало с №26
        одного из уже собранных вариантов — значит этот вариант заливали, стоп,
        не создаётся ничего. Совпадение заданий №1-25 заливке не мешает: источник
        ставит одно и то же задание в разные варианты, это норма;
      * **каждое задание** ищется по своей паре (номер, ID источника). Нашлось —
        переиспользуем с прежним id, не нашлось — создаём новое.

    Задания без ID источника (набранные через /add, залитые до появления колонки)
    ищутся по отпечатку содержимого, как раньше.
    """
    report = ImportReport(variant_number=variant_number)

    async with SessionMaker() as db:
        # 1. Вариант уже заливали?
        if variant_number is not None:
            final = next((t for t in tasks if t.number == LAST_TASK), None)
            if final is None or final.source_id is None:
                report.warnings.append(
                    f"у №{LAST_TASK} нет ID источника — проверить вариант на дубль нечем"
                )
            else:
                twin = await crud.find_variant_by_slot_source(db, LAST_TASK, final.source_id)
                if twin is not None:
                    report.duplicate_of = twin.number
                    report.total_in_db = await crud.total_tasks_count(db)
                    return report

        # 2. Что из заданий уже лежит в базе.
        by_source = await crud.find_tasks_by_source(
            db, [(t.number, t.source_id) for t in tasks if t.source_id is not None]
        )

        known_fp: dict[str, str] = {}
        without_source = sorted({t.number for t in tasks if t.source_id is None})
        for number in without_source:
            for existing in await crud.list_tasks(db, number, limit=100_000):
                key = make_fingerprint(
                    existing.number, existing.kind, existing.text, existing.passage,
                    existing.options, existing.match_left, existing.answers,
                )
                known_fp[key] = existing.id

        # Для сборки варианта нужны все задания файла, а не только новые: часть уже
        # лежит в базе, и их id берём оттуда. Иначе вариант собирался бы лишь с
        # первого раза, а повторный прогон молча его пропускал.
        for_variant: list[tuple[int, str]] = []

        for task in tasks:
            if task.source_id is not None:
                found = by_source.get((task.number, task.source_id))
            else:
                found = known_fp.get(fingerprint(task))

            if found:
                report.duplicates += 1
                report.reused.append((task.number, found))
                for_variant.append((task.number, found))
                continue

            saved = await crud.create_task(db, to_parsed(task))
            if task.source_id is not None:
                by_source[(task.number, task.source_id)] = saved.id
            else:
                known_fp[fingerprint(task)] = saved.id
            report.created.append((task.number, saved.id))
            for_variant.append((task.number, saved.id))

        # 3. Сборка варианта из смеси старых и новых заданий.
        if variant_number is not None:
            slots = {number: task_id for number, task_id in for_variant}
            if len(slots) < len(for_variant):
                report.warnings.append(
                    "в файле несколько заданий с одним номером — вариант не собран"
                )
            elif len(slots) < LAST_TASK:
                report.warnings.append(
                    f"в файле {len(slots)} номеров из {LAST_TASK} — вариант собирать не из чего"
                )
            else:
                existing = await crud.get_variant_by_number(db, variant_number)
                if existing:
                    await crud.replace_variant_items(db, existing, slots)
                    report.variant_status = "обновлён"
                else:
                    await crud.create_variant(db, variant_number, slots)
                    report.variant_status = "собран"

        report.total_in_db = await crud.total_tasks_count(db)

    return report


async def next_free_variant_number() -> int:
    """Наименьший номер варианта, который ещё не занят."""
    async with SessionMaker() as db:
        taken = {v.number for v in await crud.list_variants(db, limit=10_000)}
    return next(n for n in range(1, 10_000) if n not in taken)
