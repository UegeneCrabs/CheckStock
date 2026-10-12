import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app import db
from app.access import auth

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
    "Bas": ("Евгений Бас", "evgeny.bas@rocketteam.info"),
    "Renata": ("Ишкуватова Рената", "renata.ishkuvatova@rocketteam.info"),
    "Kitov": ("Андрей Китов", "andrey.kitov@rocketteam.info"),
    "Efimov": ("Антон Ефимов", "anton.efimov@rocketteam.info"),
    "Kipke": ("Анастасия Кипке", "kipke.anastasia@rocketteam.info"),
    "Baranova": ("Анна Баранова", "anna.baranova@rocketteam.info"),
    "Guzova": ("Татьяна Гузова", "tatyiana.guzova@gmail.com"),
    "Fomina": ("Ольга Фомина", "fominaolga226@gmail.com"),
    "test": ("Тестовый стенд", "test.stend@gmail.com"),
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

    for login, (full_name, email) in PEOPLE.items():
        if login in existing:
            skipped.append(login)
            continue
        if not PASSWORDS.get(login):
            no_password.append(login)
            continue
        todo.append((login, full_name, email))

    for login in skipped:
        print(f"  уже есть, пропускаю: {login}")
    for login in no_password:
        print(f"  ПАРОЛЬ НЕ ЗАПОЛНЕН, пропускаю: {login}")

    if not todo:
        print("\nЗаводить некого.")
        return

    print(f"\nБудет заведено: {len(todo)}")
    for login, full_name, _e in todo:
        print(f"  {login:<10} {full_name:<20} ")

    if not args.apply:
        print("\nЭто предпросмотр. Для записи добавьте --apply")
        return

    now = _now()
    for login, full_name, email in todo:
        db.create_user(
            full_name,
            email,
            login,
            auth.hash_password(PASSWORDS[login]),
            now,
        )

        print(f"  создан: {login}")

    print(f"\nГотово. Всего в базе пользователей: {len(db.list_users())}")
    print("Не забудьте очистить PASSWORDS в этом файле.")


if __name__ == "__main__":
    main()
