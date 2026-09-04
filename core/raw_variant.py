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

from core.parser import _HOMOGLYPHS, split_answer_forms, split_digit_forms
from core.tasks_meta import (
    KIND_CHOICE,
    KIND_DIGITS,
    KIND_MATCH,
    KIND_OPEN,
    LAST_TASK,
    TASK_NUMBERS,
    kind_of,
)

SOFT_HYPHEN = "­"

# Экзотические пробелы со страницы: неразрывный, узкий неразрывный, тонкий.
# Выглядят как пробел, но переносятся иначе и ломают поиск по подстроке.
EXOTIC_SPACES = str.maketrans({" ": " ", " ": " ", " ": " ", " ": " "})

# Разметка markdown: часть выгрузок со страницы проходит через конвертер, и тогда
# шапки выглядят как «↑ **Задание 2 № 45125 тип 2**», а «Пояснение.» — как
# «**Пояснение.**». Снимаем оформление, оставляя текст.
RE_BOLD = re.compile(r"\*\*(.+?)\*\*", re.S)
RE_ITALIC = re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)")
RE_HEADING = re.compile(r"^#{1,6}[ \t]*", re.M)

# Шапка блока: «↑ Задание 8 № 10262 тип 8 (решено неверно или не решено)».
# Перед словом допускаются служебные значки (стрелка возврата, остатки разметки),
# а буква «З» необязательна: при копировании у самой первой строки файла нередко
# отваливается начало («адание 1» вместо «Задание 1»).
# Съедаем строку целиком: хвост «(решено неверно...)» не должен попасть в условие.
RE_HEADER = re.compile(
    r"^[\s#>*\-–—↑•]{0,8}[Зз]?адание\s+(\d+)\s+№\s*(\d+)\s+тип\s+(\d+).*$", re.M
)

# Короткая шапка для задания, набранного руками: «Задание 8» или «Задание №8».
# Номера источника в ней нет, поэтому дубли такого задания ищутся по отпечатку.
# Буква «З» здесь обязательна: строка короткая, и без неё под шаблон попадёт
# слишком многое.
RE_HEADER_SHORT = re.compile(r"^[\s#>*\-–—↑•]{0,8}[Зз]адание\s*№?\s*(\d+)\s*[.:)]?\s*$", re.M)

# Материал в ручном наборе подписывают «Текст:» — на сайте вместо этого отдельная
# строка-маркер. Приводим одно к другому, чтобы дальше работал общий разбор.
RE_MANUAL_PASSAGE = re.compile(r"^[\s#>*\-]{0,8}(?:Текст|Материал)\s*[:\-—]\s*", re.M)

# Границы внутри блока. Ведущие значки допускаются по той же причине.
RE_EXPLANATION = re.compile(r"^[\s#>*\-]{0,8}Пояснение\.\s*$", re.M)
RE_PASSAGE = re.compile(
    r"^[\s#>*\-]{0,8}Прочитайте текст и выполните задани[ея]\.\s*$", re.M
)

# Ответы. «Ответ:» перечисляет все засчитываемые формы, «Правильный ответ:» —
# только одну, поэтому второй источник запасной и о нём сообщается отдельно.
# Двоеточие должно идти сразу после слова: у №8 в пояснении есть заголовок
# «Ответы в порядке, соответствующем буквам:», и он не ответ, а шапка таблицы.
# Обычная подпись ответа. Двоеточие сразу после слова: у №8 в пояснении есть
# заголовок «Ответы в порядке, соответствующем буквам:» — это шапка таблицы.
RE_ANSWER_PLAIN = re.compile(r"^Ответы?:\s*(.+)$", re.M)

# У части заданий обычной строки нет, зато приводятся две трактовки — «Ответ в
# демоверсии:» и «Ответ редакции:». Тогда объединяем формы: расхождение
# источника не повод засчитать ученику ошибку.
#
# Только тогда: под заданием бывает обсуждение, и реплика там подписана так же
# («Ответ редакции: Артём, Вы, однако же, ошибаетесь...»). Если обычная строка
# «Ответ:» есть, она главная, а всё остальное — разговоры.
RE_ANSWER_LABELLED = re.compile(
    r"^Ответ\s+(?:в\s+демоверсии|редакции):\s*(.+)$", re.M
)
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

# Конец столбцов задания на соответствие: дальше идёт таблица для ответа, а не
# материал. «Запишите в таблицу…» и «Запишите в ответ…» — обе формулировки живые.
RE_MATCH_TAIL = re.compile(r"^Запишите\b", re.I)

# Строчная буква — по ней отличается заголовок столбца от его содержимого.
RE_LOWER = re.compile(r"[а-яёa-z]")


def is_column_header(line: str) -> bool:
    """Заголовок столбца: «ПРЕДЛОЖЕНИЯ», «ГРАММАТИЧЕСКИЕ ОШИБКИ» и подобные.

    Признак — ни одной строчной буквы. Заголовки в источнике всегда набраны
    прописными, а строки материала — обычным текстом, так что путаницы нет.
    Сюда же попадает строка-шапка таблицы ответа «А Б В Г Д».
    """
    return bool(re.search(r"[А-ЯЁA-Z]", line)) and not RE_LOWER.search(line)


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

    Шесть вещей, каждая из которых иначе ломает поиск:
      * мягкий перенос U+00AD — невидим, но рвёт слова («За­да­ние»);
      * экзотические пробелы — выглядят обычными, но это другие символы;
      * разметка markdown — если страницу сохраняли конвертером, шапка выглядит
        как «↑ **Задание 2 № 45125 тип 2**», и слово тонет в звёздочках;
      * дубли соседних строк — тот же конвертер повторяет часть строк дважды,
        из-за чего варианты ответа нумеруются как 1, 2, 3, 3, 4, 4, 5, 5;
      * строки из одних пробелов — при другом способе копирования их не будет,
        поэтому считаем их обычными пустыми, а не разделителем;
      * латинские буквы в метках столбца — «A)» вместо «А)».
    """
    text = raw.replace(SOFT_HYPHEN, "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.translate(EXOTIC_SPACES)

    # Оформление снимаем до всего остального: дальше ищем по чистому тексту.
    text = RE_BOLD.sub(r"\1", text)
    text = RE_ITALIC.sub(r"\1", text)
    text = RE_HEADING.sub("", text)

    lines = []
    previous = None
    for line in text.split("\n"):
        line = "" if not line.strip() else line.rstrip()
        # Только первый символ метки: внутри задания латиница бывает законной.
        if len(line) > 1 and line[1] == ")":
            line = line[0].translate(_HOMOGLYPHS) + line[1:]
        # Повтор строки подряд — артефакт выгрузки. Пустые не трогаем: они
        # разделяют абзацы, и по ним же отделяется условие от материала.
        if line and line == previous:
            continue
        previous = line
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


def take_condition(body: str, kind: str) -> tuple[str, str]:
    """Условие -> (условие, остаток).

    Границу ищем двумя способами и берём ту, что встретится раньше:
      * конец первого абзаца — опора на разметку;
      * первая метка «1)» или «А)» — опора на структуру задания.

    Одного мало. Пустой строки перед списком вариантов нет в markdown-выгрузках,
    и тогда «первый абзац» проглатывает весь список. А метка есть не у всех
    видов, и у №21 список в материале — не варианты ответа, поэтому её ищем
    только там, где вид обещает варианты.

    Сверх того условие обязано содержать глагол-команду: формулировка ЕГЭ всегда
    кончается командой, и это ловит случай, когда граница всё-таки уехала.
    """
    lines = body.split("\n")
    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1
    if start >= len(lines):
        raise ValueError("пустое тело задания")

    paragraph_end = len(lines)
    for index in range(start, len(lines)):
        if not lines[index].strip():
            paragraph_end = index
            break

    cut = paragraph_end
    if kind in (KIND_CHOICE, KIND_MATCH):
        pattern = RE_LEFT if kind == KIND_MATCH else RE_OPTION
        # Ищем только внутри первого абзаца: если метка дальше, абзац и так
        # обрывается раньше и служит границей сам.
        for index in range(start, paragraph_end):
            found = pattern.match(lines[index].strip())
            if found and (kind != KIND_MATCH or found.group(1) in MATCH_LETTERS):
                cut = index
                break

    condition = squeeze("\n".join(lines[start:cut]))
    if not condition:
        raise ValueError("условие пустое — список вариантов начинается сразу после шапки")
    if not RE_COMMAND.search(condition):
        raise ValueError(
            f"в условии нет глагола-команды, граница выделена неверно: {condition[:80]!r}"
        )
    return condition, "\n".join(lines[cut:])


def take_options(rest: str) -> tuple[list[str], list[int], str, str]:
    """Пронумерованные варианты -> (варианты, их номера, что было до, что после).

    Пустая строка закрывает список: если следующий непустой абзац начинается не
    с метки, значит варианты кончились и дальше идёт что-то другое. Без этого
    правила текст-источник прилипает к последнему варианту — так и случилось на
    markdown-выгрузке, где нет строки «Прочитайте текст и выполните задание».

    Перенос варианта на новую строку при этом не страдает: он идёт сразу под
    своей меткой, без пустой строки между ними.
    """
    options: list[str] = []
    numbers: list[int] = []
    before: list[str] = []
    after: list[str] = []
    started = False
    closed = False
    blank_seen = False

    for line in rest.split("\n"):
        stripped = line.strip()

        if closed:
            after.append(line)
            continue

        found = RE_OPTION.match(stripped)
        if found:
            started = True
            blank_seen = False
            numbers.append(int(found.group(1)))
            options.append(found.group(2).strip())
            continue

        if not stripped:
            if started:
                blank_seen = True
            else:
                before.append(line)
            continue

        if not started:
            before.append(line)
        elif blank_seen:
            # Непустой абзац после пустой строки, и это не метка — список позади.
            closed = True
            after.append(line)
        else:
            # продолжение предыдущего варианта, перенесённое на новую строку
            options[-1] = f"{options[-1]} {stripped}".strip()

    return options, numbers, "\n".join(before).strip(), "\n".join(after).strip()


def take_match(rest: str) -> tuple[list[str], list[str], list[str], list[str]]:
    """Соответствие -> (левый столбец А-Д, правый столбец 1-9, буквы, лишние строки).

    **Позиция столбца занимает столько строк, сколько ей нужно.** В №22 это почти
    всегда двустишие, в прозе — фраза, разбитая переносом. Раньше бралась только
    строка с меткой, а всё остальное молча пропадало — вместе с приёмом, который
    как раз во второй строке и сидел:

        А)  Людей неинтересных в мире нет.
            Их судьбы — как истории планет.     <- эту строку теряли
        ответ: сравнение                        <- а оно только здесь и есть

    Из-за этого №22 был испорчен во всех девяти собранных вариантах, и ученик
    не мог ответить верно иначе как угадав. Задание стоит 2 балла.

    Пустая строка позицию не закрывает: выгрузка расставляет их между строками
    одного и того же двустишия. Закрывают позицию только метка следующей
    («Б)», «3)»), заголовок столбца прописными и строка «Запишите…», после
    которой идёт таблица для ответа.

    Строки, не попавшие никуда, возвращаются четвёртым значением — молча
    выбрасывать их нельзя, иначе следующая перемена в разметке источника опять
    пройдёт незамеченной.
    """
    left: list[list[str]] = []
    right: list[list[str]] = []
    letters: list[str] = []
    stray: list[str] = []
    current: list[str] | None = None

    for line in rest.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if RE_MATCH_TAIL.match(stripped):
            break

        found_left = RE_LEFT.match(stripped)
        if found_left and found_left.group(1) in MATCH_LETTERS:
            letters.append(found_left.group(1))
            left.append([found_left.group(2).strip()])
            current = left[-1]
            continue

        found_right = RE_OPTION.match(stripped)
        if found_right:
            right.append([found_right.group(2).strip()])
            current = right[-1]
            continue

        if is_column_header(stripped):
            current = None
            continue

        if current is None:
            stray.append(stripped)
        else:
            current.append(stripped)

    return (
        [squeeze(" ".join(parts)) for parts in left],
        [squeeze(" ".join(parts)) for parts in right],
        letters,
        stray,
    )


def take_answer(explanation: str) -> tuple[str, bool]:
    """Ответ -> (строка ответа, взят ли запасной источник).

    «Ответ:» перечисляет все засчитываемые формы («вследствие ИЛИ ввиду ИЛИ
    из-за»), «Правильный ответ:» — только одну. Поэтому основной источник первый,
    но у части заданий его в исходнике нет, и тогда берём второй с пометкой.
    """
    found = RE_ANSWER_PLAIN.search(explanation)
    if found:
        return found.group(1).strip(), False

    labelled = RE_ANSWER_LABELLED.findall(explanation)
    if labelled:
        # Разные трактовки одного задания. Склеиваем через «ИЛИ», дальше их
        # разберёт split_answer_forms.
        return " ИЛИ ".join(part.strip() for part in labelled), False
    found = RE_ANSWER_ONE.search(explanation)
    if found:
        return found.group(1).strip(), True
    raise ValueError("не нашёл ни «Ответ:», ни «Правильный ответ:»")


def digits_of(value: str) -> list[int]:
    return [int(ch) for ch in re.findall(r"\d", value)]


# ---------------------------------------------------------------------------
# Сборка
# ---------------------------------------------------------------------------
def _build_task(number: int, source_id: int | None, block: str, result: Result):
    """Блок текста одного задания -> (задание, текст-источник) или None.

    None значит «не собралось»: причина уже лежит в result.problems, и вызывающая
    сторона обязана ничего не сохранять. Отсюда её зовут двое — разбор целого
    варианта и разбор одного задания, присланного в админ-бота текстом, — и
    правила у них общие: вид по номеру, ответ из пояснения, при неоднозначности
    отказ.
    """
    kind = kind_of(number)
    body, passage, explanation = split_block(block)

    try:
        condition, rest = take_condition(body, kind)
        answer_raw, fallback = take_answer(explanation)
    except ValueError as exc:
        result.problems.append(Problem(number, str(exc)))
        return None

    if fallback:
        result.notes.append(
            f"№{number}: строки «Ответ:» нет, взят «Правильный ответ:» — "
            "там только одна форма, проверьте синонимы вручную"
        )

    task: dict = {
        "number": number,
        # Постоянный номер задания у источника — «Задание 8 № 10262 тип 8».
        # По нему при заливке опознаётся уже залитое задание и повторный вариант.
        "source_id": source_id,
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
        return None

    if kind == KIND_CHOICE:
        options, numbers, before, after = take_options(rest)
        if len(options) < 2:
            result.problems.append(
                Problem(number, f"вид choice, но вариантов найдено {len(options)}")
            )
            return None
        if numbers != list(range(1, len(numbers) + 1)):
            result.problems.append(
                Problem(number, f"нумерация вариантов идёт не подряд: {numbers}")
            )
            return None
        correct = digits_of(forms[0])
        outside = [c for c in correct if not 1 <= c <= len(options)]
        if outside:
            result.problems.append(
                Problem(number, f"в ответе есть {outside}, а вариантов всего {len(options)}")
            )
            return None
        task["options"] = options
        task["correct"] = sorted(set(correct))
        task["material"] = "\n\n".join(p for p in (before, after) if p) or None

    elif kind == KIND_MATCH:
        left, right, letters, stray = take_match(rest)
        if stray:
            # Не ошибка, но и не норма: разметка источника изменилась, и часть
            # текста не легла ни в один столбец. Пусть будет видно при вычитке.
            result.notes.append(
                f"№{number}: строки вне столбцов, проверьте разбор: "
                + "; ".join(s[:60] for s in stray[:3])
            )
        if list(letters) != list(MATCH_LETTERS):
            result.problems.append(
                Problem(number, f"левый столбец {letters}, ожидался {list(MATCH_LETTERS)}")
            )
            return None
        if len(right) < 2:
            result.problems.append(
                Problem(number, f"правый столбец пуст или короткий: {len(right)} позиций")
            )
            return None
        # Порядок здесь обязателен: каждой букве своя цифра.
        correct = digits_of(forms[0])
        if len(correct) != len(left):
            result.problems.append(
                Problem(number, f"в ответе {len(correct)} цифр, а слева {len(left)} позиций")
            )
            return None
        outside = [c for c in correct if not 1 <= c <= len(right)]
        if outside:
            result.problems.append(
                Problem(number, f"в ответе есть {outside}, а справа всего {len(right)} позиций")
            )
            return None
        task["match_left"] = left
        task["options"] = right
        task["correct"] = correct

    else:  # open, digits
        task["material"] = rest.strip() or None
        if kind == KIND_DIGITS:
            # Ответ — набор цифр, и только он. Приписки источника («порядок не
            # важен», «в любой последовательности») к ответу не относятся: раньше
            # такой кусок считался отдельной формой без цифр и валил весь вариант.
            task["answers"] = split_digit_forms(answer_raw)
            if not task["answers"]:
                result.problems.append(
                    Problem(number, f"вид digits, но цифр в ответе нет: {answer_raw!r}")
                )
                return None
        else:
            task["answers"] = forms
    return task, passage


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

        built = _build_task(number, int(header.group(2)), block, result)
        if built is None:
            continue
        task, passage = built

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

    # Выгрузка через конвертер теряет строку «Прочитайте текст и выполните
    # задание», и текст-источник оседает в материале каждого задания, которое к
    # нему относится. Отличить его от настоящего материала можно по повтору:
    # материал принадлежит одному заданию, общий текст дословно повторяется у
    # нескольких. Такой выносим в texts и заменяем ссылкой.
    repeats: dict[str, int] = {}
    for task in result.tasks:
        if task.get("material"):
            key = squeeze(task["material"])
            repeats[key] = repeats.get(key, 0) + 1

    for task in result.tasks:
        material = task.get("material")
        if not material or task.get("text_ref"):
            continue
        key = squeeze(material)
        if repeats.get(key, 0) < 2:
            continue
        for index, (known, _) in enumerate(passages, start=1):
            if known == key:
                task["text_ref"] = f"t{index}"
                break
        else:
            passages.append((key, material))
            task["text_ref"] = f"t{len(passages)}"
        task["material"] = None

    result.texts = {f"t{i}": full for i, (_, full) in enumerate(passages, start=1)}

    missing = [n for n in TASK_NUMBERS if n not in seen]
    if missing:
        result.problems.append(Problem(None, f"в файле нет заданий: {missing}"))

    result.tasks.sort(key=lambda t: t["number"])
    return result


PASSAGE_MARK = "Прочитайте текст и выполните задание."


def _prepare_single(text: str) -> str:
    """Дотягивает ручной набор до вида, в котором задание печатает сайт.

    Разметку только добавляем, ничего не выбрасывая:
      * «Текст: …» и всё до конца сообщения — материал задания, он получает такой
        же маркер, как на сайте;
      * если «Пояснение.» не написали, оно подставляется перед строкой «Ответ:».

    Порядок на выходе жёсткий: условие, варианты, материал, пояснение с ответом.
    В ручном шаблоне «Текст:» пишут последним, после ответа, и без перестановки
    материал уехал бы в пояснение, а задание осталось бы без него.

    Скопированное с сайта сюда не попадает — там своя разметка, и трогать её
    нечем и незачем.
    """
    if RE_HEADER.search(text):
        return text

    passage = ""
    found = RE_MANUAL_PASSAGE.search(text)
    if found and not RE_PASSAGE.search(text):
        passage = text[found.end():].strip()
        text = text[:found.start()].rstrip()

    explanation = ""
    if not RE_EXPLANATION.search(text):
        answer = RE_ANSWER_PLAIN.search(text)
        if answer:
            explanation = "Пояснение.\n" + text[answer.start():].strip()
            text = text[:answer.start()].rstrip()

    parts = [text]
    if passage:
        parts.append(f"{PASSAGE_MARK}\n{passage}")
    if explanation:
        parts.append(explanation)
    return "\n\n".join(parts)


def parse_one(raw: str) -> Result:
    """Разбор одного задания, присланного в админ-бота текстом.

    Правила те же, что у целого варианта, и код тот же: вид берётся из номера, а не
    из формы сообщения; ответ читается из пояснения; при любой неоднозначности
    задание не собирается. Отличий два:

      * не требуется весь набор №1-26 — задание пришло одно;
      * шапка может быть короткой, «Задание 8» вместо «Задание 8 № 10262 тип 8».
        Тогда ID источника пустой, и дубли такого задания ищутся по отпечатку
        содержимого, как у всего, что набрано руками.
    """
    text = _prepare_single(normalize(raw))
    result = Result()

    header = RE_HEADER.search(text)
    if header:
        number = int(header.group(1))
        source_id = int(header.group(2))
        declared = int(header.group(3))
        rest = text[header.end():]
    else:
        short = RE_HEADER_SHORT.search(text)
        if short is None:
            result.problems.append(Problem(None, (
                "первая строка должна быть шапкой задания: «Задание 8 № 10262 тип 8» — "
                "так его печатает сайт. Если набираете руками, довольно «Задание 8»"
            )))
            return result
        number = declared = int(short.group(1))
        source_id = None
        rest = text[short.end():]

    if not 1 <= number <= LAST_TASK:
        result.problems.append(Problem(number, f"номер вне диапазона 1-{LAST_TASK}"))
        return result
    # Источник печатает номер дважды — бесплатная проверка разбора шапки.
    if declared != number:
        result.problems.append(
            Problem(number, f"в шапке «тип {declared}» не совпал с номером задания")
        )
        return result
    # Несколько заданий одним сообщением не берём: у каждого свой ID и своя
    # карточка на вычитку, и вперемешку их не проверить.
    if RE_HEADER.search(rest) or RE_HEADER_SHORT.search(rest):
        result.problems.append(Problem(number, (
            "в сообщении больше одного задания. Пришлите по одному, "
            "а целый вариант — через /upload файлом"
        )))
        return result

    built = _build_task(number, source_id, strip_service(rest), result)
    if built is None:
        return result

    task, passage = built
    if passage:
        result.texts["t1"] = passage
        task["text_ref"] = "t1"
    result.tasks.append(task)
    return result


# ---------------------------------------------------------------------------
# Читаемый разбор — чтобы проверить глазами до заливки
# ---------------------------------------------------------------------------
KIND_NAMES = {
    KIND_OPEN: "вписать слово",
    KIND_CHOICE: "выбрать варианты",
    KIND_DIGITS: "вписать цифры",
    KIND_MATCH: "соответствие",
}

RULE = "=" * 58
THIN = "-" * 58


def _wrap(text: str, width: int = 92) -> str:
    """Мягкий перенос длинных строк: файл читают с телефона."""
    out = []
    for paragraph in (text or "").split("\n"):
        while len(paragraph) > width:
            cut = paragraph.rfind(" ", 0, width)
            if cut <= 0:
                cut = width
            out.append(paragraph[:cut])
            paragraph = paragraph[cut:].lstrip()
        out.append(paragraph)
    return "\n".join(out)


def render_preview(payload: dict, notes: list[str] | None = None) -> str:
    """Разобранный вариант в виде, пригодном для вычитки.

    Тексты-источники печатаются один раз в начале: один и тот же текст относится
    к нескольким заданиям, и повторять его четыре раза значит утопить в нём
    остальное. В заданиях остаётся ссылка.
    """
    tasks = payload.get("tasks") or []
    texts = payload.get("texts") or {}
    lines: list[str] = [
        RULE,
        f"РАЗБОР ВАРИАНТА — заданий {len(tasks)}",
        RULE,
        "",
        "Проверьте и подтвердите кнопкой в боте. В базу пока ничего не залито.",
        "",
    ]

    if notes:
        lines.append("ОБРАТИТЕ ВНИМАНИЕ")
        lines += [f"  • {note}" for note in notes]
        lines.append("")

    if texts:
        users = {key: [] for key in texts}
        for task in tasks:
            if task.get("text_ref") in users:
                users[task["text_ref"]].append(task["number"])
        lines += [RULE, "ТЕКСТЫ-ИСТОЧНИКИ", RULE, ""]
        for key, body in texts.items():
            where = ", ".join(f"№{n}" for n in users.get(key, [])) or "ни к кому"
            lines += [f"[{key}] — к заданиям: {where}", "", _wrap(body), "", THIN, ""]

    for task in tasks:
        number = task["number"]
        kind = task.get("kind") or ""
        options = task.get("options") or []
        left = task.get("match_left") or []
        correct = task.get("correct") or []
        answers = task.get("answers") or []

        source = task.get("source_id")
        head = f"ЗАДАНИЕ {number}   ·   {KIND_NAMES.get(kind, kind)}"
        if source:
            head += f"   ·   источник № {source}"
        lines += [
            RULE,
            head,
            RULE,
            "",
            "УСЛОВИЕ",
            _wrap(task.get("text") or ""),
            "",
        ]

        if task.get("text_ref"):
            lines += [f"ТЕКСТ: [{task['text_ref']}] — напечатан выше", ""]

        if task.get("material"):
            lines += ["СОДЕРЖИМОЕ", _wrap(task["material"]), ""]

        if kind == KIND_MATCH:
            lines.append("СОПОСТАВЛЕНИЕ")
            for index, position in enumerate(left):
                letter = MATCH_LETTERS[index] if index < len(MATCH_LETTERS) else str(index + 1)
                chosen = correct[index] if index < len(correct) else None
                target = options[chosen - 1] if chosen and chosen <= len(options) else "???"
                lines += [
                    _wrap(f"  {letter}) {position}"),
                    _wrap(f"      -> {chosen}) {target}"),
                    "",
                ]
            lines += ["ОТВЕТ", "  " + "".join(str(c) for c in correct), ""]
        elif options:
            lines.append("ВАРИАНТЫ   (>> — верный)")
            for index, option in enumerate(options, start=1):
                # Пометка идёт в начале строки: в конце её отрывает переносом.
                mark = ">>" if index in correct else "  "
                lines.append(_wrap(f" {mark} {index}) {option}"))
            lines += ["", "ОТВЕТ", "  " + ", ".join(str(c) for c in correct), ""]
        else:
            lines += ["ОТВЕТ", "  " + "   или   ".join(answers), ""]

    lines += [RULE, "ВСЕ ОТВЕТЫ СПИСКОМ", RULE, ""]
    for task in tasks:
        correct = task.get("correct") or []
        answers = task.get("answers") or []
        if task.get("kind") == KIND_MATCH:
            shown = "".join(str(c) for c in correct)
        elif correct:
            shown = "".join(str(c) for c in correct)
        else:
            shown = " / ".join(answers)
        lines.append(f"  №{task['number']:<3} {shown}")

    return "\n".join(lines) + "\n"
