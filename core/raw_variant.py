"""Разбор сырого варианта с РЕШУ ЕГЭ в структуру заданий.

Чистые функции: на вход строка, скопированная со страницы, на выходе `Result` со
списком заданий, текстами и списком проблем. Ни файлов, ни базы, ни сети —
поэтому одну и ту же логику зовут и `scripts/parse_raw.py`, и админ-бот.

Главное правило: **при любой неоднозначности задание не собирается**, а попадает
в `problems`. Прошлый парсер разбирал сырой текст эвристиками, на №13 молча
потерял вариант ответа и сдвинул правильные ответы — ошибку заметили не сразу.
Вызывающая сторона обязана проверить `problems` и ничего не сохранять, если он
не пуст.

Что известно заранее и не выводится из вёрстки:
  * вид задания берётся из `KIND_BY_NUMBER` по номеру;
  * условие — первый абзац после шапки, и в нём обязан быть глагол-команда;
  * порядок цифр в ответе важен только у `match` (столбец А-Д).
"""
import re
from dataclasses import dataclass, field

from core.parser import _HOMOGLYPHS
from core.tasks_meta import (
    KIND_CHOICE,
    KIND_DIGITS,
    KIND_MATCH,
    LAST_TASK,
    TASK_NUMBERS,
    kind_of,
)

SOFT_HYPHEN = "­"

# Экзотические пробелы со страницы: неразрывный, узкий неразрывный, тонкий.
# Выглядят как пробел, но переносятся иначе и ломают поиск по подстроке.
EXOTIC_SPACES = str.maketrans({" ": " ", " ": " ", " ": " ", " ": " "})

# Шапка блока: «↑ Задание 8 № 10262 тип 8 (решено неверно или не решено)».
# Первые символы необязательны — при копировании у самой первой строки файла
# нередко отваливается начало («адание 1» вместо «Задание 1»).
# Съедаем строку целиком: хвост «(решено неверно...)» не должен попасть в условие.
RE_HEADER = re.compile(r"^.{0,3}?адание\s+(\d+)\s+№\s*(\d+)\s+тип\s+(\d+).*$", re.M)

# Границы внутри блока.
RE_EXPLANATION = re.compile(r"^Пояснение\.\s*$", re.M)
RE_PASSAGE = re.compile(r"^Прочитайте текст и выполните задани[ея]\.\s*$", re.M)

# Ответы. «Ответ:» перечисляет все засчитываемые формы, «Правильный ответ:» —
# только одну, поэтому второй источник запасной и о нём сообщается отдельно.
# Двоеточие должно идти сразу после слова: у №8 в пояснении есть заголовок
# «Ответы в порядке, соответствующем буквам:», и он не ответ, а шапка таблицы.
RE_ANSWER_FULL = re.compile(r"^Ответы?:\s*(.+)$", re.M)
RE_ANSWER_ONE = re.compile(r"^Ваш ответ:.*?Правильный ответ:\s*(.+)$", re.M)

# Метки вариантов. Латинские двойники приводятся к кириллице при чистке:
# в №22 источник печатает столбец как «A) Б) В) Г) Д)» — первая буква латинская.
RE_OPTION = re.compile(r"^(\d{1,2})\)\s*(.*)$")
RE_LEFT = re.compile(r"^([А-Я])\)\s*(.*)$")

# Формулировка задания ЕГЭ всегда содержит команду. Если её нет — условие
# выделилось неверно, и разбирать дальше нельзя.
RE_COMMAND = re.compile(
    r"\b(запишите|укажите|напишите|исправьте|отредактируйте|найдите|подберите"
    r"|установите|расставьте|выпишите|определите|отметьте|заполните)\b",
    re.I,
)

# Строки страницы, не относящиеся к заданию. Строку «Ваш ответ: ... Правильный
# ответ: X» здесь НЕ трогаем: она лежит в пояснении и служит запасным источником
# ответа для заданий, где нет строки «Ответ:».
RE_SERVICE = re.compile(
    r"^(Дополнительно|Правило|Пункт правила[\s\d.]*|Спрятать пояснение.*)$",
    re.I,
)

MATCH_LETTERS = ("А", "Б", "В", "Г", "Д")


@dataclass
class Problem:
    number: int | None
    message: str

    def __str__(self) -> str:
        where = f"Задание {self.number}" if self.number else "Файл"
        return f"{where}: {self.message}"


@dataclass
class Result:
    tasks: list[dict] = field(default_factory=list)
    texts: dict[str, str] = field(default_factory=dict)
    problems: list[Problem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems and bool(self.tasks)

    def payload(self) -> dict:
        """Структура файла варианта — её принимает core.variant_import."""
        return {"texts": self.texts, "tasks": self.tasks}


# ---------------------------------------------------------------------------
# Чистка
# ---------------------------------------------------------------------------
def normalize(raw: str) -> str:
    """Приводит сырой текст к виду, пригодному для разбора.

    Четыре вещи, каждая из которых иначе ломает поиск:
      * мягкий перенос U+00AD — невидим, но рвёт слова («За­да­ние»);
      * экзотические пробелы — выглядят обычными, но это другие символы;
      * строки из одних пробелов — при другом способе копирования их не будет,
        поэтому считаем их обычными пустыми, а не разделителем;
      * латинские буквы в метках столбца — «A)» вместо «А)».
    """
    text = raw.replace(SOFT_HYPHEN, "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.translate(EXOTIC_SPACES)
    lines = []
    for line in text.split("\n"):
        line = "" if not line.strip() else line.rstrip()
        # Только первый символ метки: внутри задания латиница бывает законной.
        if len(line) > 1 and line[1] == ")":
            line = line[0].translate(_HOMOGLYPHS) + line[1:]
        lines.append(line)
    return "\n".join(lines)


def strip_service(text: str) -> str:
    return "\n".join(ln for ln in text.split("\n") if not RE_SERVICE.match(ln.strip()))


def paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def squeeze(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Разбор одного задания
# ---------------------------------------------------------------------------
def split_block(block: str) -> tuple[str, str, str]:
    """Блок -> (задание, текст-источник, пояснение)."""
    parts = RE_EXPLANATION.split(block, maxsplit=1)
    body, explanation = parts[0], (parts[1] if len(parts) > 1 else "")

    parts = RE_PASSAGE.split(body, maxsplit=1)
    body, passage = parts[0], (parts[1] if len(parts) > 1 else "")
    return body, passage.strip(), explanation


def take_condition(body: str) -> tuple[str, str]:
    """Условие -> (условие, остаток).

    Два признака должны выполняться одновременно: условие занимает ровно первый
    абзац, и в нём есть глагол-команда. Опора сразу на разметку и на смысл —
    если копирование склеит абзацы, второй признак это поймает.
    """
    pars = paragraphs(body)
    if not pars:
        raise ValueError("пустое тело задания")
    condition = squeeze(pars[0])
    if not RE_COMMAND.search(condition):
        raise ValueError(
            f"в первом абзаце нет глагола-команды, условие выделено неверно: {condition[:80]!r}"
        )
    return condition, "\n\n".join(pars[1:])


def take_options(rest: str) -> tuple[list[str], list[int], str]:
    """Пронумерованные варианты -> (варианты, их номера, то что было до них)."""
    options: list[str] = []
    numbers: list[int] = []
    before: list[str] = []
    started = False
    for line in rest.split("\n"):
        stripped = line.strip()
        found = RE_OPTION.match(stripped)
        if found:
            started = True
            numbers.append(int(found.group(1)))
            options.append(found.group(2).strip())
        elif started and stripped:
            # продолжение предыдущего варианта, перенесённое на новую строку
            options[-1] = f"{options[-1]} {stripped}".strip()
        elif not started:
            before.append(line)
    return options, numbers, "\n".join(before).strip()


def take_match(rest: str) -> tuple[list[str], list[str], list[str]]:
    """Соответствие -> (левый столбец А-Д, правый столбец 1-9, буквы слева)."""
    left: list[str] = []
    letters: list[str] = []
    right: list[str] = []
    for line in rest.split("\n"):
        stripped = line.strip()
        found_left = RE_LEFT.match(stripped)
        if found_left and found_left.group(1) in MATCH_LETTERS:
            letters.append(found_left.group(1))
            left.append(found_left.group(2).strip())
            continue
        found_right = RE_OPTION.match(stripped)
        if found_right:
            right.append(found_right.group(2).strip())
    return left, right, letters


def take_answer(explanation: str) -> tuple[str, bool]:
    """Ответ -> (строка ответа, взят ли запасной источник).

    «Ответ:» перечисляет все засчитываемые формы («вследствие ИЛИ ввиду ИЛИ
    из-за»), «Правильный ответ:» — только одну. Поэтому основной источник первый,
    но у части заданий его в исходнике нет, и тогда берём второй с пометкой.
    """
    found = RE_ANSWER_FULL.search(explanation)
    if found:
        return found.group(1).strip(), False
    found = RE_ANSWER_ONE.search(explanation)
    if found:
        return found.group(1).strip(), True
    raise ValueError("не нашёл ни «Ответ:», ни «Правильный ответ:»")


def split_answer_forms(raw: str) -> list[str]:
    """Строка ответа -> список равнозначных форм.

    Источник разделяет их по-разному: «12|21», «вследствие ИЛИ ввиду», «135, 351».
    """
    raw = raw.strip().rstrip(".")
    forms = [f.strip(" .,;") for f in re.split(r"\s+ИЛИ\s+|\s*\|\s*", raw)]
    return [f for f in forms if f]


def digits_of(value: str) -> list[int]:
    return [int(ch) for ch in re.findall(r"\d", value)]


# ---------------------------------------------------------------------------
# Сборка
# ---------------------------------------------------------------------------
def parse(raw: str) -> Result:
    """Сырой текст варианта -> задания, тексты, проблемы."""
    text = normalize(raw)
    result = Result()
    headers = list(RE_HEADER.finditer(text))

    if not headers:
        result.problems.append(
            Problem(None, "не нашёл ни одной строки «Задание N № … тип N» — это точно вариант?")
        )
        return result

    passages: list[tuple[str, str]] = []  # (сжатый для сравнения, полный)
    seen: set[int] = set()

    for position, header in enumerate(headers):
        number = int(header.group(1))
        declared = int(header.group(3))
        end = headers[position + 1].start() if position + 1 < len(headers) else len(text)
        block = strip_service(text[header.end():end])

        if number in seen:
            result.problems.append(Problem(number, "номер встречается второй раз"))
            continue
        seen.add(number)

        if not 1 <= number <= LAST_TASK:
            result.problems.append(Problem(number, f"номер вне диапазона 1-{LAST_TASK}"))
            continue
        # Источник печатает номер дважды — бесплатная проверка разбора шапки.
        if declared != number:
            result.problems.append(
                Problem(number, f"в шапке «тип {declared}» не совпал с номером задания")
            )
            continue

        kind = kind_of(number)
        body, passage, explanation = split_block(block)

        try:
            condition, rest = take_condition(body)
            answer_raw, fallback = take_answer(explanation)
        except ValueError as exc:
            result.problems.append(Problem(number, str(exc)))
            continue

        if fallback:
            result.notes.append(
                f"№{number}: строки «Ответ:» нет, взят «Правильный ответ:» — "
                "там только одна форма, проверьте синонимы вручную"
            )

        task: dict = {
            "number": number,
            "kind": kind,
            "text": condition,
            "text_ref": None,
            "material": None,
            "options": [],
            "match_left": [],
            "correct": [],
            "answers": [],
        }

        forms = split_answer_forms(answer_raw)
        if not forms:
            result.problems.append(Problem(number, f"пустой ответ: {answer_raw!r}"))
            continue

        if kind == KIND_CHOICE:
            options, numbers, before = take_options(rest)
            if len(options) < 2:
                result.problems.append(
                    Problem(number, f"вид choice, но вариантов найдено {len(options)}")
                )
                continue
            if numbers != list(range(1, len(numbers) + 1)):
                result.problems.append(
                    Problem(number, f"нумерация вариантов идёт не подряд: {numbers}")
                )
                continue
            correct = digits_of(forms[0])
            outside = [c for c in correct if not 1 <= c <= len(options)]
            if outside:
                result.problems.append(
                    Problem(number, f"в ответе есть {outside}, а вариантов всего {len(options)}")
                )
                continue
            task["options"] = options
            task["correct"] = sorted(set(correct))
            task["material"] = before or None

        elif kind == KIND_MATCH:
            left, right, letters = take_match(rest)
            if list(letters) != list(MATCH_LETTERS):
                result.problems.append(
                    Problem(number, f"левый столбец {letters}, ожидался {list(MATCH_LETTERS)}")
                )
                continue
            if len(right) < 2:
                result.problems.append(
                    Problem(number, f"правый столбец пуст или короткий: {len(right)} позиций")
                )
                continue
            # Порядок здесь обязателен: каждой букве своя цифра.
            correct = digits_of(forms[0])
            if len(correct) != len(left):
                result.problems.append(
                    Problem(number, f"в ответе {len(correct)} цифр, а слева {len(left)} позиций")
                )
                continue
            outside = [c for c in correct if not 1 <= c <= len(right)]
            if outside:
                result.problems.append(
                    Problem(number, f"в ответе есть {outside}, а справа всего {len(right)} позиций")
                )
                continue
            task["match_left"] = left
            task["options"] = right
            task["correct"] = correct

        else:  # open, digits
            task["material"] = rest.strip() or None
            task["answers"] = forms
            if kind == KIND_DIGITS and not all(any(ch.isdigit() for ch in f) for f in forms):
                result.problems.append(
                    Problem(number, f"вид digits, но в ответе нет цифр: {forms}")
                )
                continue

        # Тексты повторяются после каждого задания, которое к ним относится:
        # храним по одному экземпляру, заданию ставим ссылку.
        if passage:
            key = squeeze(passage)
            for index, (known, _) in enumerate(passages, start=1):
                if known == key:
                    task["text_ref"] = f"t{index}"
                    break
            else:
                passages.append((key, passage))
                task["text_ref"] = f"t{len(passages)}"

        result.tasks.append(task)

    result.texts = {f"t{i}": full for i, (_, full) in enumerate(passages, start=1)}

    missing = [n for n in TASK_NUMBERS if n not in seen]
    if missing:
        result.problems.append(Problem(None, f"в файле нет заданий: {missing}"))

    result.tasks.sort(key=lambda t: t["number"])
    return result
