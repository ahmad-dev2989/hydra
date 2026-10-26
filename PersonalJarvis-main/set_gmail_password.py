"""
Utility script to configure Google App Password in HYDRA Vault & .env
Usage: python set_gmail_password.py "<16-char-app-password>" [email]
"""
import sys
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from plugins.vault_manager import VAULT_MGR

def configure_gmail_password(password: str, email: str = "f240004@cfd.nu.edu.pk"):
    clean_pwd = password.strip().replace(" ", "")
    if not clean_pwd:
        print("[ERROR] Password cannot be empty.")
        return False

    # 1. Update in Vault
    VAULT_MGR.set_account_password("gmail", clean_pwd, email)

    # 2. Update in .env
    env_file = BASE_DIR / ".env"
    lines = []
    found = False
    if env_file.exists():
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("GMAIL_APP_PASSWORD="):
                    lines.append(f"GMAIL_APP_PASSWORD={clean_pwd}\n")
                    found = True
                else:
                    lines.append(line)
    if not found:
        lines.append(f"GMAIL_APP_PASSWORD={clean_pwd}\n")

    with open(env_file, "w", encoding="utf-8") as f:
        f.writelines(lines)

    print(f"[SUCCESS] Configured Google App Password for {email} in Vault and .env!")
    return True

if __name__ == "__main__":
    if len(sys.argv) > 1:
        pwd = sys.argv[1]
        acc = sys.argv[2] if len(sys.argv) > 2 else "f240004@cfd.nu.edu.pk"
        configure_gmail_password(pwd, acc)
    else:
        print("Usage: python set_gmail_password.py <16_character_app_password> [account_email]")
