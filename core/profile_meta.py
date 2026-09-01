"""Анкета ученика: класс, набор предметов ЕГЭ и цель по баллам.

Чистый модуль без БД и сети — как `tasks_meta`. Здесь и справочник, и правила
проверки: сервер обязан проверять ответы анкеты сам, потому что клиент можно
подменить, а данные потом лягут в профиль и в подбор материалов.

Правила набора предметов взяты из порядка сдачи ЕГЭ:
  * русский язык сдают все — он не выбирается, а подставляется;
  * математика обязательна, но одна из двух: профильная или базовая;
  * профильная математика идёт как предмет по выбору, поэтому сверх неё нужен
    ещё один предмет. С базовой — два, она в конкурсные баллы не входит.
"""

GRADES = (7, 8, 9, 10, 11)

# --- математика ---------------------------------------------------------------
MATH_PROFILE = "profile"
MATH_BASE = "base"
MATH_LEVELS: dict[str, str] = {
    MATH_PROFILE: "Профильная математика",
    MATH_BASE: "Базовая математика",
}

# Сколько предметов по выбору нужно сверх русского и математики. Число строгое:
# и меньше, и больше — ошибка, иначе анкета не описывает реальный набор экзаменов.
EXTRA_SUBJECTS_REQUIRED: dict[str, int] = {
    MATH_PROFILE: 1,
    MATH_BASE: 2,
}

# --- предметы -----------------------------------------------------------------
# Русский язык сдают все, поэтому в выбор он не входит и хранить его у каждого
# пользователя незачем — он подставляется при показе.
RUSSIAN = "Русский язык"

SUBJECTS: dict[str, str] = {
    "informatics": "Информатика",
    "history": "История",
    "social": "Обществознание",
    "chemistry": "Химия",
    "biology": "Биология",
    "english": "Английский язык",
    "physics": "Физика",
    "literature": "Литература",
}

# --- цель по баллам -----------------------------------------------------------
TARGETS: dict[str, str] = {
    "200_230": "200–230 баллов",
    "230_260": "230–260 баллов",
    "260_plus": "Больше 260 баллов",
}


def subject_title(key: str) -> str:
    return SUBJECTS.get(key, key)


def math_title(level: str | None) -> str:
    return MATH_LEVELS.get(level or "", "")


def target_title(key: str | None) -> str:
    return TARGETS.get(key or "", "")


def extra_required(math_level: str | None) -> int:
    return EXTRA_SUBJECTS_REQUIRED.get(math_level or "", 0)


def exam_list(math_level: str | None, subjects: list[str] | None) -> list[str]:
    """Полный список экзаменов ученика — то, что показывается в профиле.

    Русский и математика идут первыми и всегда: их ученик не выбирал, но сдаёт.
    """
    exams = [RUSSIAN]
    if math_level in MATH_LEVELS:
        exams.append(MATH_LEVELS[math_level])
    for key in subjects or []:
        exams.append(subject_title(key))
    return exams


def validate(grade, math_level, subjects, target) -> str | None:
    """Проверяет анкету целиком. None — всё в порядке, иначе текст ошибки.

    Текст пишется для человека: он доходит до Mini App как есть.
    """
    if grade not in GRADES:
        return f"класс должен быть от {GRADES[0]} до {GRADES[-1]}"

    if math_level not in MATH_LEVELS:
        return "нужно выбрать математику: профильную или базовую"

    if not isinstance(subjects, list):
        return "предметы должны приходить списком"

    unknown = [s for s in subjects if s not in SUBJECTS]
    if unknown:
        return f"неизвестные предметы: {', '.join(map(str, unknown))}"

    if len(set(subjects)) != len(subjects):
        return "предмет выбран дважды"

    required = EXTRA_SUBJECTS_REQUIRED[math_level]
    if len(subjects) != required:
        word = "предмет" if required == 1 else "предмета"
        with_what = "профильной" if math_level == MATH_PROFILE else "базовой"
        return (
            f"с {with_what} математикой нужно выбрать ровно {required} {word} "
            f"по выбору, а выбрано {len(subjects)}"
        )

    if target not in TARGETS:
        return "нужно выбрать желаемый результат по баллам"

    return None


def options_payload() -> dict:
    """Справочник для экрана анкеты: всё, из чего ученик выбирает."""
    return {
        "grades": list(GRADES),
        "math_levels": [
            {
                "key": key,
                "title": name,
                "extra_required": EXTRA_SUBJECTS_REQUIRED[key],
            }
            for key, name in MATH_LEVELS.items()
        ],
        "subjects": [{"key": key, "title": name} for key, name in SUBJECTS.items()],
        "targets": [{"key": key, "title": name} for key, name in TARGETS.items()],
        "always": [RUSSIAN],
    }
