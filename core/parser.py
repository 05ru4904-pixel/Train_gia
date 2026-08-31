"""Разбор текстовых шаблонов админ-бота (ТЗ п.16, 18, 19).

Всё на чистых функциях: на вход строка от админа, на выход структура или ParseError
с человеческим текстом ошибки, который бот показывает как есть.

Шаблон один на все четыре вида заданий — вид определяется по форме сообщения, а не
по номеру. Так админ пишет то, что видит перед глазами, а не сверяется с таблицей:

    выбор вариантов      варианты «А) …» + «Ответ: А, В»
    вписать слово        вариантов нет  + «Ответ: заклятым, злейшим»
    вписать цифры        вариантов нет  + «Ответ: 134»
    соответствие         «А) …» и «1) …» + «Ответ: А-3, Б-1»

Номер подсказывает только там, где форма неоднозначна (см. parse_task).
Материал задания — необязательный хвост «Текст: …» до конца сообщения.
"""
import re
import secrets
from dataclasses import dataclass, field

from core.tasks_meta import (
    KIND_CHOICE,
    KIND_DIGITS,
    KIND_MATCH,
    KIND_OPEN,
    LAST_TASK,
    kind_of,
    letter,
    letter_index,
)

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

# Правый столбец задания на соответствие: «1) Приехав в город, …».
_RE_MATCH_RIGHT = re.compile(r"^\s*(\d{1,2})\s*[).:\-—]\s*(.+?)\s*$")
# Пара в ответе на соответствие: «А-3», «А) 3», «А:3», «А3».
_RE_MATCH_PAIR = re.compile(r"^([А-ЯA-Z])\s*[-–—:.)]*\s*(\d{1,2})$")
# Материал задания: всё от этой строки и до конца сообщения.
_RE_PASSAGE = re.compile(r"^\s*(?:текст|материал)\s*[:\-—]\s*(.*)$", re.I)

_ANSWER_MISSING = (
    "Не нашёл строку с ответом.\n"
    "Последняя строка должна быть вида: <b>Ответ: А</b>, <b>Ответ: А, В, Д</b>, "
    "<b>Ответ: заклятым</b> или <b>Ответ: А-3, Б-1</b>"
)


class ParseError(ValueError):
    """Ошибка разбора с текстом, пригодным для показа админу."""


@dataclass
class ParsedTask:
    """Разобранное задание любого вида.

    Поля, которых нет у конкретного вида, остаются пустыми: у задания с вписыванием
    ответа нет options, у выбора вариантов — answers. Слой БД пишет их как есть.
    """

    number: int
    text: str
    options: list[str]
    correct: list[int]
    kind: str = KIND_CHOICE
    answers: list[str] = field(default_factory=list)
    match_left: list[str] = field(default_factory=list)
    passage: str | None = None
    # Номер задания на сайте-источнике («Задание 8 № 10262 тип 8»). Постоянный: не
    # меняется от правки текста, поэтому по нему и опознаётся уже залитое задание.
    # У заданий, набранных админом через /add, его нет — там остаётся None.
    source_id: int | None = None

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


# Разделители равнозначных форм ответа. Их источник ставит осознанно, в отличие
# от запятой, которая бывает и частью самого ответа.
_RE_FORM_SPLIT = re.compile(r"\s+ИЛИ\s+|\s*\|\s*|\s*;\s*")

# То же для ответов из цифр, но «или» здесь считается разделителем в любом регистре:
# внутри цифрового ответа это слово ничем другим быть не может, а у словесного —
# может («так или иначе»).
_RE_DIGIT_SPLIT = re.compile(r"\s+или\s+|\s*\|\s*|\s*;\s*", re.I)


def split_digit_forms(raw: str) -> list[str]:
    """Строка ответа задания с цифрами -> формы, в которых остались только цифры.

    У таких заданий ответ — набор цифр, и всё, что источник дописал рядом, к
    ответу не относится: «25; порядок не важен», «34 ИЛИ 43 в любом порядке»,
    «1234 (в любой последовательности)». Раньше кусок без единой цифры считался
    отдельной формой ответа, и на нём отклонялся весь вариант.

    Запятая внутри формы разделителем НЕ считается — она перечисляет цифры одного
    ответа. Отсюда два случая, и различаются они набором цифр:
      * «145, 541» — части составлены из одних и тех же цифр, это перестановки
        одного ответа, хватит первой;
      * «3, 5» — наборы разные, значит перечислены цифры одного ответа, склеиваем
        в «35». Иначе ученик, записавший «35», получил бы «неверно», а написавший
        «3» — «верно», то есть ровно наоборот.

    Порядок цифр внутри формы сохраняем как есть: сверяет ответы `core/scoring.py`,
    и там порядок не важен, но в карточке админ должен видеть запись источника.
    """
    forms: list[str] = []
    seen: set[tuple] = set()

    for part in _RE_DIGIT_SPLIT.split(raw or ""):
        pieces = [re.sub(r"\D", "", piece) for piece in part.split(",")]
        pieces = [piece for piece in pieces if piece]
        if not pieces:
            continue   # кусок без цифр — приписка источника, не форма ответа

        first = sorted(pieces[0])
        if all(sorted(piece) == first for piece in pieces[1:]):
            value = pieces[0]
        else:
            value = "".join(pieces)

        key = tuple(sorted(value))
        if key not in seen:
            seen.add(key)
            forms.append(value)
    return forms


def split_answer_forms(raw: str) -> list[str]:
    """Строка ответа -> список равнозначных форм.

    Разделять их можно четырьмя способами: «вследствие ИЛИ ввиду», «12|21»,
    «в конце концов; может быть», «135, 351».

    С запятой осторожно: она делит формы только когда части — отдельные слова
    или цифры. У развёрнутого ответа («не столько планы, сколько нас») запятая
    принадлежит самой фразе, и разрезать по ней значит забраковать ученика,
    который написал верно целиком.

    Для заданий с цифрами есть свой разбор — `split_digit_forms`.
    """
    raw = raw.strip().rstrip(".")
    parts = [p.strip(" .") for p in _RE_FORM_SPLIT.split(raw)]

    forms: list[str] = []
    for part in parts:
        if not part:
            continue
        pieces = [piece.strip() for piece in part.split(",")]
        if len(pieces) > 1 and all(pieces) and not any(" " in piece for piece in pieces):
            forms.extend(pieces)
        else:
            forms.append(part)

    seen: set[str] = set()
    unique: list[str] = []
    for form in forms:
        key = form.lower()
        if key not in seen:
            seen.add(key)
            unique.append(form)
    return unique


# --------------------------------------------------------------------------- #
# Разбор одного задания
# --------------------------------------------------------------------------- #
def _split_passage(raw: str) -> tuple[str, str | None]:
    """Отрезает хвост «Текст: …» — материал, к которому относится задание.

    Материал идёт последним и тянется до конца сообщения: он бывает на десятки
    строк, и любой признак конца пришлось бы отличать от самого текста.
    """
    lines = raw.strip().splitlines()
    for i, line in enumerate(lines):
        found = _RE_PASSAGE.match(line)
        if found:
            tail = [found.group(1), *lines[i + 1:]]
            passage = "\n".join(tail).strip()
            return "\n".join(lines[:i]), passage or None
    return raw, None


def _find_answer_line(body: list[str]) -> int | None:
    """Номер строки с ответом. Ищем с конца: «Ответ:» — последняя строка шаблона,
    а в условии слово «ответ» встречается сплошь и рядом."""
    for i in range(len(body) - 1, -1, -1):
        if _RE_ANSWER.match(body[i]):
            return i
    return None


@dataclass
class _Columns:
    """Собранные столбцы задания: буквенный слева, цифровой справа."""

    left: list[str] = field(default_factory=list)
    right: list[str] = field(default_factory=list)
    text: list[str] = field(default_factory=list)
    letters: list[str] = field(default_factory=list)
    indexes: list[int] = field(default_factory=list)
    system: str | None = None
    first_left_at: int | None = None


def _collect(body: list[str], answer_at: int, numbered: bool) -> _Columns:
    """Разбирает тело задания на условие и столбцы.

    Строка считается вариантом, только если её буква идёт после предыдущей: иначе
    «А. Пушкин» внутри условия будет принят за вариант ответа. Строка, не похожая
    ни на что, продолжает последний собранный пункт — длинные варианты переносятся.
    """
    result = _Columns()
    current: list[str] | None = None

    for i, line in enumerate(body):
        if i == answer_at:
            continue

        option = _RE_OPTION.match(line)
        if option and not result.right:
            char = option.group(1)
            system = result.system or _label_system(char)
            index = _index_in(char, system)
            if index > (result.indexes[-1] if result.indexes else -1):
                result.system = system
                result.left.append(option.group(2).strip())
                result.letters.append(char.upper())
                result.indexes.append(index)
                if result.first_left_at is None:
                    result.first_left_at = i
                current = result.left
                continue

        if numbered:
            right = _RE_MATCH_RIGHT.match(line)
            if right and int(right.group(1)) == len(result.right) + 1:
                result.right.append(right.group(2).strip())
                current = result.right
                continue

        if current is not None:
            current[-1] = f"{current[-1]} {line}".strip()
        else:
            result.text.append(line)
    return result


def _looks_like_match_answer(raw: str, left_count: int) -> bool:
    """Ответ на соответствие: «А-3, Б-1» или голая последовательность «31»."""
    tokens = [t for t in re.split(r"[,\s;/]+", raw.strip()) if t]
    if not tokens:
        return False
    if all(_RE_MATCH_PAIR.match(t.upper()) for t in tokens):
        return True
    digits = "".join(tokens)
    return digits.isdigit() and len(digits) == left_count


def parse_task(raw: str) -> ParsedTask:
    """Разбирает шаблон одного задания любого вида.

    Вид определяется по форме сообщения. Единственный неоднозначный случай — ровно
    одна буквенная строка: «А) звонИт» это либо оборванный список вариантов, либо
    «А. Пушкин» в условии задания с вписыванием ответа. Здесь и только здесь спор
    решает номер: у ФИПИ за каждым номером закреплён свой вид (KIND_BY_NUMBER).
    """
    body_raw, passage = _split_passage(raw)
    lines = [ln.strip() for ln in body_raw.strip().splitlines() if ln.strip()]
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

    body = lines[1:]
    answer_at = _find_answer_line(body)
    if answer_at is None:
        raise ParseError(_ANSWER_MISSING)
    answer_raw = _RE_ANSWER.match(body[answer_at]).group(1)

    columns = _collect(body, answer_at, numbered=True)
    if (
        len(columns.left) >= 2
        and len(columns.right) >= 2
        and _looks_like_match_answer(answer_raw, len(columns.left))
    ):
        return _parse_match(number, columns, answer_raw, answer_at, passage)

    columns = _collect(body, answer_at, numbered=False)
    if len(columns.left) >= 2 or (columns.left and kind_of(number) == KIND_CHOICE):
        return _parse_choice(number, columns, answer_raw, answer_at, passage)

    return _parse_typed(number, body, answer_at, answer_raw, passage)


def _parse_choice(number, columns: _Columns, answer_raw, answer_at, passage) -> ParsedTask:
    text = "\n".join(columns.text).strip()
    if not text:
        raise ParseError("Не нашёл текст задания между номером и вариантами ответа.")
    if len(columns.left) < 2:
        raise ParseError(
            "Нужно минимум 2 варианта ответа.\n"
            "Каждый вариант — с новой строки: <b>А) текст</b>, затем <b>Б) текст</b> и так далее."
        )
    if columns.first_left_at is not None and answer_at < columns.first_left_at:
        # «Ответ» нашёлся раньше вариантов — значит это слово из условия, а
        # настоящей строки ответа в сообщении нет.
        raise ParseError(_ANSWER_MISSING)

    system = columns.system or "cyr"
    if columns.indexes != list(range(len(columns.left))):
        expected = [_label_in(i, system) for i in range(len(columns.left))]
        raise ParseError(
            "Буквы вариантов идут не подряд: получилось "
            + ", ".join(columns.letters)
            + ".\nОжидалось: "
            + ", ".join(expected)
            + ". Проверьте, не пропущена ли буква."
        )

    correct = _parse_answer(answer_raw, len(columns.left), system)
    return ParsedTask(
        number=number,
        text=text,
        options=columns.left,
        correct=correct,
        kind=KIND_CHOICE,
        passage=passage,
    )


def _parse_match(number, columns: _Columns, answer_raw, answer_at, passage) -> ParsedTask:
    text = "\n".join(columns.text).strip()
    if not text:
        raise ParseError("Не нашёл текст задания между номером и столбцами.")
    if columns.first_left_at is not None and answer_at < columns.first_left_at:
        raise ParseError(_ANSWER_MISSING)

    system = columns.system or "cyr"
    if columns.indexes != list(range(len(columns.left))):
        expected = [_label_in(i, system) for i in range(len(columns.left))]
        raise ParseError(
            "Буквы левого столбца идут не подряд: получилось "
            + ", ".join(columns.letters)
            + ".\nОжидалось: "
            + ", ".join(expected)
            + "."
        )

    correct = _parse_match_answer(answer_raw, len(columns.left), len(columns.right), system)
    return ParsedTask(
        number=number,
        text=text,
        options=columns.right,
        correct=correct,
        kind=KIND_MATCH,
        match_left=columns.left,
        passage=passage,
    )


def _parse_typed(number, body: list[str], answer_at, answer_raw, passage) -> ParsedTask:
    """Задание с вписыванием ответа: цифры или слово."""
    text = "\n".join(line for i, line in enumerate(body) if i != answer_at).strip()
    if not text:
        raise ParseError("Не нашёл текст задания между номером и строкой ответа.")

    answers = split_answer_forms(answer_raw)
    if not answers:
        raise ParseError("Строка «Ответ:» пустая.")

    # Вид берём из таблицы ФИПИ: она знает, что у №25 ответ — номер предложения, но
    # сверять его надо как слово. Если номер к вписыванию не относится (админ убрал
    # варианты у задания на выбор), решает содержимое ответа.
    kind = kind_of(number)
    if kind not in (KIND_OPEN, KIND_DIGITS):
        kind = KIND_DIGITS if all(a.isdigit() for a in answers) else KIND_OPEN
    if kind == KIND_DIGITS:
        # Из ответа берём только цифры: приписки вроде «порядок не важен» к нему
        # не относятся и раньше валили разбор.
        answers = split_digit_forms(answer_raw)
        if not answers:
            raise ParseError(
                f"У задания №{number} ответ — цифры, а в строке «Ответ:» их нет: {answer_raw}"
            )

    return ParsedTask(
        number=number,
        text=text,
        options=[],
        correct=[],
        kind=kind,
        answers=answers,
        passage=passage,
    )


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


def _parse_match_answer(raw: str, left_count: int, right_count: int, system: str) -> list[int]:
    """Ответ на соответствие -> позиция справа для каждой позиции слева, по порядку.

    Порядок здесь и есть ответ, поэтому пары раскладываются по буквам, а не по тому,
    в каком порядке админ их написал: «Б-1, А-3» и «А-3, Б-1» — одно и то же.
    """
    tokens = [t for t in re.split(r"[,\s;/]+", raw.strip()) if t]
    joined = "".join(tokens)

    if joined.isdigit() and len(joined) == left_count and not any(
        _RE_MATCH_PAIR.match(t.upper()) and not t.isdigit() for t in tokens
    ):
        values = [int(ch) for ch in joined]
    else:
        by_left: dict[int, int] = {}
        for token in tokens:
            pair = _RE_MATCH_PAIR.match(token.upper())
            if not pair:
                raise ParseError(
                    f"Не понял «{token}» в ответе.\n"
                    "Каждой букве слева нужна цифра справа: <b>Ответ: А-3, Б-1, В-5</b>"
                )
            index = _index_in(pair.group(1), system)
            if index < 0 or index >= left_count:
                raise ParseError(
                    f"В ответе есть буква «{pair.group(1)}», а слева всего {left_count} "
                    f"позиций (от {_label_in(0, system)} до {_label_in(left_count - 1, system)})."
                )
            if index in by_left:
                raise ParseError(f"Позиция «{pair.group(1)}» указана в ответе дважды.")
            by_left[index] = int(pair.group(2))

        missing = [_label_in(i, system) for i in range(left_count) if i not in by_left]
        if missing:
            raise ParseError(
                "В ответе не хватает позиций: " + ", ".join(missing) + "."
            )
        values = [by_left[i] for i in range(left_count)]

    bad = [v for v in values if not 1 <= v <= right_count]
    if bad:
        raise ParseError(
            f"В ответе есть {bad}, а справа всего {right_count} позиций."
        )
    return values


# --------------------------------------------------------------------------- #
# Обратная сборка: задание -> шаблон
# --------------------------------------------------------------------------- #
def to_template(task) -> str:
    """Задание -> тот же шаблон, каким его прислали бы админу.

    Нужно для правки: бот отдаёт задание готовым текстом, админ меняет строку и
    шлёт обратно. Разбор этого текста обязан вернуть то же самое задание — за этим
    следит тест round-trip, иначе правка молча теряла бы поля.

    Принимает и ParsedTask, и модель Task из базы — по именам полей.
    """
    kind = getattr(task, "kind", None) or KIND_CHOICE
    options = list(getattr(task, "options", None) or [])
    correct = list(getattr(task, "correct", None) or [])
    answers = list(getattr(task, "answers", None) or [])
    match_left = list(getattr(task, "match_left", None) or [])

    lines = [f"Задание №{task.number}", task.text]

    if kind == KIND_MATCH:
        lines += [f"{letter(i)}) {item}" for i, item in enumerate(match_left)]
        lines += [f"{i + 1}) {item}" for i, item in enumerate(options)]
        lines.append("Ответ: " + ", ".join(
            f"{letter(i)}-{value}" for i, value in enumerate(correct)
        ))
    elif kind in (KIND_OPEN, KIND_DIGITS):
        # Запятая делит формы, только когда они однословные, — иначе разделяем «|»,
        # чтобы ответ «не столько планы, сколько нас» не распался на два.
        separator = ", " if all(
            " " not in a and "," not in a for a in answers
        ) else " | "
        lines.append("Ответ: " + separator.join(answers))
    else:
        lines += [f"{letter(i)}) {option}" for i, option in enumerate(options)]
        lines.append("Ответ: " + ", ".join(letter(i) for i in correct))

    passage = getattr(task, "passage", None)
    if passage:
        lines.append(f"Текст: {passage}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Варианты и пакеты
# --------------------------------------------------------------------------- #
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
