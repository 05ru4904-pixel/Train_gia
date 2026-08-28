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


def split_answer_forms(raw: str) -> list[str]:
    """Строка ответа -> список равнозначных форм.

    Источник разделяет их четырьмя способами: «вследствие ИЛИ ввиду», «12|21»,
    «в конце концов; может быть», «135, 351».

    С запятой осторожно: она делит формы только когда части — отдельные слова
    или цифры. У развёрнутого ответа («не столько планы, сколько нас») запятая
    принадлежит самой фразе, и разрезать по ней значит забраковать ученика,
    который написал верно целиком.
    """
    raw = raw.strip().rstrip(".")
    parts = [p.strip(" .") for p in re.split(r"\s+ИЛИ\s+|\s*\|\s*|\s*;\s*", raw)]

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
            condition, rest = take_condition(body, kind)
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
            options, numbers, before, after = take_options(rest)
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
            task["material"] = "\n\n".join(p for p in (before, after) if p) or None

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

        lines += [
            RULE,
            f"ЗАДАНИЕ {number}   ·   {KIND_NAMES.get(kind, kind)}",
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
