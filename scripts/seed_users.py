import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import auth, db

PASSWORDS = {
    "Renata": "",
    "Bas": "",
    "Kitov": "",
    "Efimov": "",
    "Kipke": "",
    "Baranova": "",
    "Guzova": "",
    "Fomina": "",
    "test": "",
}


PEOPLE = {
    "Bas": ("Евгений Бас", "evgeny.bas@rocketteam.info", "admin", False, True),
    "Renata": ("Ишкуватова Рената", "renata.ishkuvatova@rocketteam.info", "admin", False, True),
    "Kitov": ("Андрей Китов", "andrey.kitov@rocketteam.info", "user", False, True),
    "Efimov": ("Антон Ефимов", "anton.efimov@rocketteam.info", "user", False, True),
    "Kipke": ("Анастасия Кипке", "kipke.anastasia@rocketteam.info", "user", False, True),
    "Baranova": ("Анна Баранова", "anna.baranova@rocketteam.info", "user", False, True),
    "Guzova": ("Татьяна Гузова", "tatyiana.guzova@gmail.com", "user", False, True),
    "Fomina": ("Ольга Фомина", "fominaolga226@gmail.com", "user", False, True),
    "test": ("Тестовый стенд", "test.stend@gmail.com", "superadmin", False, False),
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def main() -> None:
    parser = argparse.ArgumentParser(description="Завести сотрудников в PAKETA")
    parser.add_argument("--apply", action="store_true", help="записать в базу")
    args = parser.parse_args()

    db.init_db()
    existing = {u["login"] for u in db.list_users()}

    todo, skipped, no_password = [], [], []

    for login, (full_name, email, role, can_edit, can_manage) in PEOPLE.items():
        if login in existing:
            skipped.append(login)
            continue
        if not PASSWORDS.get(login):
            no_password.append(login)
            continue
        todo.append((login, full_name, email, role, can_edit, can_manage))

    for login in skipped:
        print(f"  уже есть, пропускаю: {login}")
    for login in no_password:
        print(f"  ПАРОЛЬ НЕ ЗАПОЛНЕН, пропускаю: {login}")

    if not todo:
        print("\nЗаводить некого.")
        return

    print(f"\nБудет заведено: {len(todo)}")
    for login, full_name, _e, role, can_edit, _m in todo:
        edit = "правка стока: да" if can_edit else "правка стока: нет"
        print(f"  {login:<10} {full_name:<20} {role:<11} {edit}")

    if not args.apply:
        print("\nЭто предпросмотр. Для записи добавьте --apply")
        return

    now = _now()
    for login, full_name, email, role, can_edit, can_manage in todo:
        user_id = db.create_user(
            full_name,
            email,
            login,
            auth.hash_password(PASSWORDS[login]),
            role,
            now,
        )

        db.set_user_permission(user_id, "can_edit_stock", can_edit)
        db.set_user_permission(user_id, "can_manage_users", can_manage)
        print(f"  создан: {login}")

    print(f"\nГотово. Всего в базе пользователей: {len(db.list_users())}")
    print("Не забудьте очистить PASSWORDS в этом файле.")


if __name__ == "__main__":
    main()
