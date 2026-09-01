"""Анкета ученика: правила набора предметов. Запуск: python tests/test_profile_meta.py

Проверка живёт на сервере, потому что клиента можно подменить, а набор экзаменов
потом ляжет в профиль и в подбор материалов.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import profile_meta as pm  # noqa: E402


def test_grades():
    assert pm.GRADES == (7, 8, 9, 10, 11)
    assert pm.validate(6, pm.MATH_BASE, ["history", "physics"], "200_230")
    assert pm.validate(12, pm.MATH_BASE, ["history", "physics"], "200_230")
    assert pm.validate(7, pm.MATH_BASE, ["history", "physics"], "200_230") is None


def test_profile_math_needs_exactly_one_subject():
    assert pm.validate(11, pm.MATH_PROFILE, ["history"], "230_260") is None
    assert "ровно 1 предмет" in pm.validate(11, pm.MATH_PROFILE, [], "230_260")
    assert "ровно 1 предмет" in pm.validate(11, pm.MATH_PROFILE, ["history", "physics"], "230_260")


def test_base_math_needs_exactly_two_subjects():
    assert pm.validate(11, pm.MATH_BASE, ["history", "physics"], "230_260") is None
    assert "ровно 2 предмета" in pm.validate(11, pm.MATH_BASE, ["history"], "230_260")
    assert "ровно 2 предмета" in pm.validate(
        11, pm.MATH_BASE, ["history", "physics", "biology"], "230_260"
    )


def test_math_level_is_required():
    assert "математику" in pm.validate(11, None, ["history"], "230_260")
    assert "математику" in pm.validate(11, "advanced", ["history"], "230_260")


def test_unknown_and_repeated_subjects():
    assert "неизвестные предметы" in pm.validate(11, pm.MATH_PROFILE, ["алгебра"], "230_260")
    assert "дважды" in pm.validate(11, pm.MATH_BASE, ["history", "history"], "230_260")
    assert "списком" in pm.validate(11, pm.MATH_PROFILE, "history", "230_260")


def test_target_is_required():
    assert pm.validate(11, pm.MATH_PROFILE, ["history"], None)
    assert pm.validate(11, pm.MATH_PROFILE, ["history"], "300_plus")
    for key in pm.TARGETS:
        assert pm.validate(11, pm.MATH_PROFILE, ["history"], key) is None


def test_exam_list_starts_with_russian_and_math():
    """Русский и математику ученик не выбирал, но сдаёт — в списке они первыми."""
    exams = pm.exam_list(pm.MATH_BASE, ["history", "physics"])
    assert exams == ["Русский язык", "Базовая математика", "История", "Физика"]
    assert pm.exam_list(None, []) == ["Русский язык"]


def test_options_payload_is_complete():
    options = pm.options_payload()
    assert options["grades"] == [7, 8, 9, 10, 11]
    assert {level["key"] for level in options["math_levels"]} == {"profile", "base"}
    assert {level["extra_required"] for level in options["math_levels"]} == {1, 2}
    assert len(options["subjects"]) == 8
    assert [t["key"] for t in options["targets"]] == ["200_230", "230_260", "260_plus"]
    assert options["always"] == ["Русский язык"]


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok  {name}")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"FAIL  {name}: {exc}")
    print("--- провалено:", failed)
    sys.exit(1 if failed else 0)
