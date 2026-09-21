import sys
from pathlib import Path

# Добавляем backend в PYTHONPATH
sys.path.append(str(Path(__file__).parent.parent / "backend"))

from app.bootstrap import provision

TEST_USERS = [
    ("admin@crm.local", "Администратор", "admin"),
    ("manager@crm.local", "Руководитель", "manager"),
    ("finance@crm.local", "Владелец расчётов", "finance"),
    ("sales@crm.local", "Менеджер продаж", "sales"),
    ("buyer@crm.local", "Закупщик", "buyer"),
    ("logistics@crm.local", "Логист", "logistics"),
    ("controller@crm.local", "Финансовый контролёр", "controller")
]

def seed_users():
    for email, name, role_prefix in TEST_USERS:
        # Для локальных проверок используем пароль TestPassword123
        created = provision(email, name, "TestPassword123", all_roles=False)
        if created:
            print(f"User {name} ({email}) created.")
        else:
            print(f"User {name} ({email}) already exists.")

if __name__ == "__main__":
    seed_users()
