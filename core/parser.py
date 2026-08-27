"""Разбор текстовых шаблонов админ-бота (ТЗ п.16, 18, 19).

Всё на чистых функциях: на вход строка от админа, на выход структура или ParseError
с человеческим текстом ошибки, который бот показывает как есть.
"""
import re
import secrets
from dataclasses import dataclass, field

from core.tasks_meta import LAST_TASK, letter, letter_index

# Латиница, которую легко спутать с кириллицей. Админ рано или поздно наберёт "A)"
# вместо "А)" — молча чиним, вместо того чтобы падать.
_HOMOGLYPHS = str.maketrans({
    "A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н", "K": "К", "M": "М",
    "O": "О", "P": "Р", "T": "Т", "X": "Х", "Y": "У",
})

# Для ID наоборот: ID всегда латинские, кириллицу приводим к латинице.
_HOMOGLYPHS_ID = str.maketrans({
    "А": "A", "В": "B", "С": "C", "Е": "E", "Н": "H", "К": "K", "М": "M",
    "О": "O", "Р": "P", "Т": "T", "Х": "X", "У": "Y",
})

# Алфавит ID заданий: без 0/O и 1/I, чтобы ID нельзя было прочитать неоднозначно.
_ID_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
ID_LENGTH = 6

_LATIN_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _label_system(char: str) -> str:
    """Каким алфавитом админ нумерует варианты: латиницей или кириллицей.

    Различать обязательно: латинская последовательность A, B, C — это позиции 1, 2, 3,
    а по внешнему виду те же буквы читаются как кириллические А, В, С, то есть
    позиции 1, 3, 18. Кодовые точки у них разные, поэтому определяется однозначно.
    """
    return "lat" if char.upper() in _LATIN_LETTERS else "cyr"


def _index_in(char: str, system: str) -> int:
    """Буква -> позиция варианта внутри выбранного алфавита."""
    char = char.upper().strip()
    if system == "lat":
        return _LATIN_LETTERS.find(char.translate(_HOMOGLYPHS_ID))
    return letter_index(char.translate(_HOMOGLYPHS))


def _label_in(index: int, system: str) -> str:
    if system == "lat":
        return _LATIN_LETTERS[index] if index < len(_LATIN_LETTERS) else str(index + 1)
    return letter(index)

_RE_NUMBER = re.compile(r"^\s*(?:задание\s*)?(?:№|N|No|#)?\s*(\d{1,2})\s*[.:)]?\s*$", re.I)
_RE_OPTION = re.compile(r"^\s*([А-ЯA-Z])\s*[).:\-—]\s*(.+?)\s*$")
_RE_ANSWER = re.compile(r"^\s*ответ\w*\s*[:\-—]?\s*(.+?)\s*$", re.I)
_RE_VARIANT = re.compile(r"^\s*вариант\s*(?:№|N|No|#)?\s*(\d{1,4})\s*$", re.I)
_RE_VARIANT_ROW = re.compile(r"^\s*(\d{1,2})\s*[).:\-—]\s*([A-Za-zА-Яа-я0-9]{4,12})\s*$")


class ParseError(ValueError):
    """Ошибка разбора с текстом, пригодным для показа админу."""


@dataclass
class ParsedTask:
    number: int
    text: str
    options: list[str]
    correct: list[int]

    @property
    def correct_letters(self) -> str:
        return ", ".join(letter(i) for i in self.correct)


@dataclass
class ParsedVariant:
    number: int
    task_ids: dict[int, str] = field(default_factory=dict)


def generate_task_id() -> str:
    """Уникальный ID задания вида K7F29A (ТЗ п.15). Проверка коллизии — на стороне БД."""
    return "".join(secrets.choice(_ID_ALPHABET) for _ in range(ID_LENGTH))


def normalize_task_id(raw: str) -> str:
    return raw.strip().upper().translate(_HOMOGLYPHS_ID)


def parse_task(raw: str) -> ParsedTask:
    """Разбирает шаблон одного задания.

    Задание №4
    В каком слове правильно поставлено ударение?
    А) звонИт
    Б) звОнит
    Ответ: А
    """
    lines = [ln.strip() for ln in raw.strip().splitlines() if ln.strip()]
    if not lines:
        raise ParseError("Пустое сообщение. Пришлите задание по шаблону.")

    number_match = _RE_NUMBER.match(lines[0])
    if not number_match:
        raise ParseError(
            "Не нашёл номер задания в первой строке.\n"
            "Первая строка должна быть вида: <b>Задание №4</b>"
        )
    number = int(number_match.group(1))
    if not 1 <= number <= LAST_TASK:
        raise ParseError(f"Номер задания должен быть от 1 до {LAST_TASK}, а получил {number}.")

    text_lines: list[str] = []
    options: list[str] = []
    option_letters: list[str] = []
    option_indexes: list[int] = []
    system: str | None = None
    answer_raw: str | None = None

    for line in lines[1:]:
        answer_match = _RE_ANSWER.match(line)
        if answer_match and options:
            answer_raw = answer_match.group(1)
            continue

        option_match = _RE_OPTION.match(line)
        if option_match:
            char = option_match.group(1)
            current_system = system or _label_system(char)
            index = _index_in(char, current_system)
            last = option_indexes[-1] if option_indexes else -1
            # Строка считается вариантом, только если её буква идёт после предыдущей.
            # Иначе «А. Пушкин» внутри условия задания будет принят за вариант ответа.
            if index > last:
                system = current_system
                options.append(option_match.group(2).strip())
                option_letters.append(char.upper())
                option_indexes.append(index)
                continue

        if options:
            # строка после начала вариантов — продолжение последнего варианта
            options[-1] = f"{options[-1]} {line}".strip()
        else:
            text_lines.append(line)

    text = "\n".join(text_lines).strip()
    if not text:
        raise ParseError("Не нашёл текст задания между номером и вариантами ответа.")
    if len(options) < 2:
        raise ParseError(
            "Нужно минимум 2 варианта ответа.\n"
            "Каждый вариант — с новой строки: <b>А) текст</b>, затем <b>Б) текст</b> и так далее."
        )

    assert system is not None
    if option_indexes != list(range(len(options))):
        expected = [_label_in(i, system) for i in range(len(options))]
        raise ParseError(
            "Буквы вариантов идут не подряд: получилось "
            + ", ".join(option_letters)
            + ".\nОжидалось: "
            + ", ".join(expected)
            + ". Проверьте, не пропущена ли буква."
        )

    if answer_raw is None:
        raise ParseError(
            "Не нашёл строку с ответом.\n"
            "Последняя строка должна быть вида: <b>Ответ: А</b> или <b>Ответ: А, В, Д</b>"
        )

    correct = _parse_answer(answer_raw, len(options), system)
    return ParsedTask(number=number, text=text, options=options, correct=correct)


def _parse_answer(raw: str, option_count: int, system: str) -> list[int]:
    """Ответ читается тем же алфавитом, каким пронумерованы варианты."""
    tokens = [t for t in re.split(r"[,\s;/]+", raw.strip()) if t]
    if not tokens:
        raise ParseError("Строка «Ответ:» пустая.")
    indexes: list[int] = []
    for token in tokens:
        token = token.strip(".)")
        if token.isdigit():
            index = int(token) - 1  # допускаем «Ответ: 1, 3»
        else:
            index = _index_in(token, system)
        if index < 0 or index >= option_count:
            raise ParseError(
                f"В ответе указан вариант «{token}», но такого варианта нет — "
                f"их всего {option_count} "
                f"(от {_label_in(0, system)} до {_label_in(option_count - 1, system)})."
            )
        if index in indexes:
            raise ParseError(f"Вариант «{token}» указан в ответе дважды.")
        indexes.append(index)
    return sorted(indexes)


def parse_variant(raw: str) -> ParsedVariant:
    """Разбирает сборку варианта из существующих ID (ТЗ п.19).

    Вариант №10
    1: A72K91
    2: B82L43
    """
    lines = [ln.strip() for ln in raw.strip().splitlines() if ln.strip()]
    if not lines:
        raise ParseError("Пустое сообщение.")

    header = _RE_VARIANT.match(lines[0])
    if not header:
        raise ParseError("Первая строка должна быть вида: <b>Вариант №10</b>")
    variant = ParsedVariant(number=int(header.group(1)))

    for line in lines[1:]:
        row = _RE_VARIANT_ROW.match(line)
        if not row:
            raise ParseError(
                f"Не понял строку: <code>{line}</code>\n"
                "Каждая строка — <b>номер задания: ID</b>, например <code>1: A72K91</code>"
            )
        number = int(row.group(1))
        if not 1 <= number <= LAST_TASK:
            raise ParseError(
                f"Номер задания должен быть от 1 до {LAST_TASK}, а в строке «{line}» — {number}."
            )
        if number in variant.task_ids:
            raise ParseError(f"Задание №{number} указано в варианте дважды.")
        variant.task_ids[number] = normalize_task_id(row.group(2))

    if not variant.task_ids:
        raise ParseError("В варианте нет ни одного задания.")
    return variant


def parse_task_batch(raw: str) -> list[ParsedTask]:
    """Разбирает несколько заданий из одного сообщения (ТЗ п.18 — добавление целого
    варианта). Новый блок начинается со строки «Задание №N»."""
    chunks: list[list[str]] = []
    current: list[str] = []
    for line in raw.strip().splitlines():
        if _RE_NUMBER.match(line) and current:
            chunks.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        chunks.append(current)

    tasks: list[ParsedTask] = []
    for i, chunk in enumerate(chunks, 1):
        text = "\n".join(chunk).strip()
        if not text:
            continue
        try:
            tasks.append(parse_task(text))
        except ParseError as exc:
            raise ParseError(f"Блок №{i}: {exc}") from exc
    if not tasks:
        raise ParseError("Не нашёл ни одного задания.")
    return tasks
