import os
from models import initialize_db, User

def create_default_admin():
    # Atrof-muhit o'zgaruvchilardan standart ma'lumotlarni oling, aks holda quyidagilarni foydalaning.
    default_username = os.getenv("DEFAULT_ADMIN_USERNAME", "admin")
    default_password = os.getenv("DEFAULT_ADMIN_PASSWORD", "admin123")

    if User.get_or_none(User.username == default_username):
        print(f"✅ Admin foydalanuvchisi '{default_username}' allaqachon mavjud.")
    else:
        User.create_user(username=default_username, password=default_password, is_admin=True)
        print("🎉 Standart admin foydalanuvchisi yaratildi:")
        print(f"  Foydalanuvchi nomi: {default_username}")
        print(f"  Parol: {default_password}")

if __name__ == "__main__":
    initialize_db()
    create_default_admin()
