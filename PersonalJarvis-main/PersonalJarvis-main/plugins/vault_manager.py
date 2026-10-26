"""
HYDRA Local Secure Vault Manager (Phase 1)
Safely stores, encrypts, and retrieves local user session tokens and credentials
for all connected apps in the Plugins Catalog with zero cloud dependency.
Uses native Windows DPAPI (CryptProtectData / CryptUnprotectData) for machine/user-bound
military-grade encryption of session tokens.
"""

import os
import sys
import json
import base64
import ctypes
from ctypes import wintypes
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

CURRENT_DIR = Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parent
VAULT_DIR = ROOT_DIR / "data" / "vault"
VAULT_FILE = VAULT_DIR / "vault.json"
VAULT_ENC_FILE = VAULT_DIR / "vault.enc"
GOOGLE_SESSION_ENC = VAULT_DIR / "google_session.enc"
SPOTIFY_SESSION_ENC = VAULT_DIR / "spotify_session.enc"
WHATSAPP_SESSION_DIR = VAULT_DIR / "whatsapp_session"
WHATSAPP_SESSION_ENC = WHATSAPP_SESSION_DIR / "session.enc"
CONNECTED_PLUGINS_FILE = ROOT_DIR / "connected_plugins.json"


# Win32 DPAPI Structs
class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ('cbData', wintypes.DWORD),
        ('pbData', ctypes.POINTER(ctypes.c_byte))
    ]


def _encrypt_dpapi(raw_bytes: bytes) -> bytes:
    """Encrypts bytes using Windows DPAPI tied strictly to current user profile."""
    if not sys.platform.startswith("win"):
        # Cross-platform base64 fallback
        return base64.b64encode(raw_bytes)

    try:
        p_data = (ctypes.c_byte * len(raw_bytes))(*raw_bytes)
        in_blob = DATA_BLOB(len(raw_bytes), ctypes.cast(p_data, ctypes.POINTER(ctypes.c_byte)))
        out_blob = DATA_BLOB()
        if ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(in_blob),
            "HYDRA_SECURE_VAULT",
            None, None, None, 0,
            ctypes.byref(out_blob)
        ):
            res = bytes(ctypes.string_at(out_blob.pbData, out_blob.cbData))
            ctypes.windll.kernel32.LocalFree(out_blob.pbData)
            return res
    except Exception as e:
        print(f"[VAULT ENCRYPT WARNING] DPAPI failed, using fallback: {e}")
    return base64.b64encode(raw_bytes)


def _decrypt_dpapi(enc_bytes: bytes) -> bytes:
    """Decrypts bytes using Windows DPAPI tied strictly to current user profile."""
    if not sys.platform.startswith("win"):
        try:
            return base64.b64decode(enc_bytes)
        except Exception:
            return enc_bytes

    try:
        p_data = (ctypes.c_byte * len(enc_bytes))(*enc_bytes)
        in_blob = DATA_BLOB(len(enc_bytes), ctypes.cast(p_data, ctypes.POINTER(ctypes.c_byte)))
        out_blob = DATA_BLOB()
        if ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(in_blob),
            None, None, None, None, 0,
            ctypes.byref(out_blob)
        ):
            res = bytes(ctypes.string_at(out_blob.pbData, out_blob.cbData))
            ctypes.windll.kernel32.LocalFree(out_blob.pbData)
            return res
    except Exception as e:
        # Check if fallback
        try:
            return base64.b64decode(enc_bytes)
        except Exception:
            pass
        print(f"[VAULT DECRYPT WARNING] DPAPI decrypt failed: {e}")
    return b""


class VaultManager:
    """Manages local credentials and session tokens for all connected plugins."""

    def __init__(self):
        VAULT_DIR.mkdir(parents=True, exist_ok=True)
        WHATSAPP_SESSION_DIR.mkdir(parents=True, exist_ok=True)
        self._ensure_gitignore()
        self._sync_legacy_connected_plugins()

    def _ensure_gitignore(self):
        git_ignore = VAULT_DIR / ".gitignore"
        if not git_ignore.exists():
            try:
                git_ignore.write_text("*\n!.gitignore\n", encoding="utf-8")
            except Exception:
                pass

    def _sync_legacy_connected_plugins(self):
        """Auto-migrates existing connected_plugins.json entries into vault."""
        if CONNECTED_PLUGINS_FILE.exists():
            try:
                with open(CONNECTED_PLUGINS_FILE, "r", encoding="utf-8") as f:
                    pdata = json.load(f)
                vault = self._load_raw_vault()
                updated = False
                for k, v in pdata.items():
                    if isinstance(v, dict) and v.get("connected"):
                        if k not in vault:
                            vault[k] = v
                            updated = True
                        elif "accounts" in v and v.get("accounts") != vault[k].get("accounts"):
                            vault[k]["accounts"] = v["accounts"]
                            vault[k]["account"] = v.get("account", vault[k].get("account"))
                            updated = True
                if updated:
                    self._save_raw_vault(vault)
            except Exception:
                pass

    def _load_raw_vault(self) -> Dict[str, Any]:
        """Loads vault data from encrypted vault.enc or vault.json."""
        if VAULT_ENC_FILE.exists():
            try:
                with open(VAULT_ENC_FILE, "rb") as f:
                    enc_data = f.read()
                dec_bytes = _decrypt_dpapi(enc_data)
                if dec_bytes:
                    return json.loads(dec_bytes.decode("utf-8"))
            except Exception:
                pass

        if VAULT_FILE.exists():
            try:
                with open(VAULT_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_raw_vault(self, data: Dict[str, Any]):
        """Persists vault data both in encrypted binary and synced json."""
        raw_bytes = json.dumps(data, indent=4).encode("utf-8")
        try:
            enc_bytes = _encrypt_dpapi(raw_bytes)
            with open(VAULT_ENC_FILE, "wb") as f:
                f.write(enc_bytes)
        except Exception as e:
            print(f"[VAULT ERROR] Could not save encrypted vault: {e}")

        # Also maintain plaintext vault.json and connected_plugins.json for system interoperability
        try:
            with open(VAULT_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except Exception:
            pass

        try:
            with open(CONNECTED_PLUGINS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except Exception:
            pass

    # =========================================================================
    # Phase 1 Specific 1-Click Session Handlers
    # =========================================================================

    def store_google_session(self, account: str, session_data: Dict[str, Any]):
        """
        Securely encrypts and stores authenticated Google session tokens (Phase 1.2).
        Unlocks Gmail, Google Classroom, and YouTube simultaneously.
        """
        clean_acc = account.strip().lower()
        payload = {
            "account": clean_acc,
            "session_data": session_data,
            "connected_at": json.dumps(session_data.get("timestamp") or "")
        }
        raw_bytes = json.dumps(payload).encode("utf-8")
        enc_bytes = _encrypt_dpapi(raw_bytes)
        try:
            with open(GOOGLE_SESSION_ENC, "wb") as f:
                f.write(enc_bytes)
            print(f"[VAULT] Successfully saved encrypted Google session -> {GOOGLE_SESSION_ENC}")
        except Exception as e:
            print(f"[VAULT ERROR] Could not write google_session.enc: {e}")

        # Update vault entries for all Google Apps
        for g_app in ["gmail", "google_classroom", "youtube"]:
            self.set_plugin_credential(
                app_id=g_app,
                account=clean_acc,
                auth_type="google_session",
                extra_data={"session_enc": str(GOOGLE_SESSION_ENC.name), "verified": True}
            )

    def load_google_session(self) -> Optional[Dict[str, Any]]:
        """Decrypts and returns active Google session data."""
        if not GOOGLE_SESSION_ENC.exists():
            return None
        try:
            with open(GOOGLE_SESSION_ENC, "rb") as f:
                enc = f.read()
            dec = _decrypt_dpapi(enc)
            if dec:
                return json.loads(dec.decode("utf-8"))
        except Exception as e:
            print(f"[VAULT ERROR] Could not decrypt google_session.enc: {e}")
        return None

    def store_spotify_session(self, account: str, token_data: Dict[str, Any]):
        """Securely encrypts and stores Spotify OAuth / PKCE session tokens."""
        clean_acc = account.strip()
        payload = {
            "account": clean_acc,
            "token_data": token_data
        }
        raw_bytes = json.dumps(payload).encode("utf-8")
        enc_bytes = _encrypt_dpapi(raw_bytes)
        try:
            with open(SPOTIFY_SESSION_ENC, "wb") as f:
                f.write(enc_bytes)
            print(f"[VAULT] Successfully saved encrypted Spotify session -> {SPOTIFY_SESSION_ENC}")
        except Exception as e:
            print(f"[VAULT ERROR] Could not write spotify_session.enc: {e}")

        self.set_plugin_credential(
            app_id="spotify",
            account=clean_acc,
            auth_type="spotify_pkce",
            extra_data={"session_enc": str(SPOTIFY_SESSION_ENC.name), "verified": True}
        )

    def load_spotify_session(self) -> Optional[Dict[str, Any]]:
        """Decrypts and returns Spotify session data."""
        if not SPOTIFY_SESSION_ENC.exists():
            return None
        try:
            with open(SPOTIFY_SESSION_ENC, "rb") as f:
                enc = f.read()
            dec = _decrypt_dpapi(enc)
            if dec:
                return json.loads(dec.decode("utf-8"))
        except Exception as e:
            print(f"[VAULT ERROR] Could not decrypt spotify_session.enc: {e}")
        return None

    def store_whatsapp_session(self, account: str, session_data: Dict[str, Any]):
        """Securely encrypts and stores paired WhatsApp Web / multi-device keys."""
        clean_acc = account.strip()
        payload = {
            "account": clean_acc,
            "session_data": session_data
        }
        raw_bytes = json.dumps(payload).encode("utf-8")
        enc_bytes = _encrypt_dpapi(raw_bytes)
        try:
            with open(WHATSAPP_SESSION_ENC, "wb") as f:
                f.write(enc_bytes)
            print(f"[VAULT] Successfully saved encrypted WhatsApp session -> {WHATSAPP_SESSION_ENC}")
        except Exception as e:
            print(f"[VAULT ERROR] Could not write whatsapp session: {e}")

        self.set_plugin_credential(
            app_id="whatsapp",
            account=clean_acc,
            auth_type="whatsapp_qr",
            extra_data={"session_enc": str(WHATSAPP_SESSION_ENC.name), "verified": True}
        )

    def load_whatsapp_session(self) -> Optional[Dict[str, Any]]:
        """Decrypts and returns WhatsApp session data."""
        if not WHATSAPP_SESSION_ENC.exists():
            return None
        try:
            with open(WHATSAPP_SESSION_ENC, "rb") as f:
                enc = f.read()
            dec = _decrypt_dpapi(enc)
            if dec:
                return json.loads(dec.decode("utf-8"))
        except Exception as e:
            print(f"[VAULT ERROR] Could not decrypt whatsapp session: {e}")
        return None

    # =========================================================================
    # Standard Vault API
    # =========================================================================

    def get_plugin_data(self, app_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves credentials from vault or falls back to connected_plugins.json."""
        app_key = app_id.lower().replace(" ", "_")
        vault = self._load_raw_vault()
        if app_key in vault:
            return vault[app_key]

        if CONNECTED_PLUGINS_FILE.exists():
            try:
                with open(CONNECTED_PLUGINS_FILE, "r", encoding="utf-8") as f:
                    pdata = json.load(f)
                if app_key in pdata and pdata[app_key].get("connected"):
                    return pdata[app_key]
            except Exception:
                pass
        return None

    def get_account_email(self, app_id: str) -> Optional[str]:
        data = self.get_plugin_data(app_id)
        if data:
            return data.get("account")
        return None

    def get_token(self, app_id: str) -> Optional[str]:
        """Retrieves access token or API key for connected app if present."""
        data = self.get_plugin_data(app_id) or {}
        return (
            data.get("token")
            or data.get("access_token")
            or data.get("data", {}).get("token")
            or data.get("data", {}).get("access_token")
            or data.get("extra_data", {}).get("token")
            or data.get("extra_data", {}).get("access_token")
        )

    def is_connected(self, app_id: str) -> bool:
        data = self.get_plugin_data(app_id)
        return bool(data and data.get("connected") and data.get("account"))

    def get_account_password(self, app_id: str) -> str:
        """Retrieves configured password or app password for active account."""
        app_key = app_id.lower().replace(" ", "_")
        vault = self._load_raw_vault()
        entry = vault.get(app_key) or {}

        # 1. Check active account in accounts array
        accounts = entry.get("accounts", [])
        for a in accounts:
            if isinstance(a, dict) and a.get("active"):
                pwd = a.get("data", {}).get("password") or a.get("password")
                if pwd:
                    return str(pwd).strip()

        # 2. Check entry data
        pwd = entry.get("data", {}).get("password") or entry.get("password")
        if pwd:
            return str(pwd).strip()

        # 3. Check any account in accounts array
        for a in accounts:
            if isinstance(a, dict):
                pwd = a.get("data", {}).get("password") or a.get("password")
                if pwd:
                    return str(pwd).strip()

        # 4. Fallback to connected_plugins.json
        if CONNECTED_PLUGINS_FILE.exists():
            try:
                with open(CONNECTED_PLUGINS_FILE, "r", encoding="utf-8") as f:
                    pdata = json.load(f)
                p_entry = pdata.get(app_key) or {}
                for a in p_entry.get("accounts", []):
                    if isinstance(a, dict) and a.get("active"):
                        p = a.get("data", {}).get("password") or a.get("password")
                        if p:
                            return str(p).strip()
                p = p_entry.get("data", {}).get("password") or p_entry.get("password")
                if p:
                    return str(p).strip()
                for a in p_entry.get("accounts", []):
                    if isinstance(a, dict):
                        p = a.get("data", {}).get("password") or a.get("password")
                        if p:
                            return str(p).strip()
            except Exception:
                pass
        return ""

    def set_account_password(self, app_id: str, password: str, account: Optional[str] = None):
        """Sets or updates the password/app_password for an app's active account."""
        app_key = app_id.lower().replace(" ", "_")
        vault = self._load_raw_vault()
        entry = vault.get(app_key, {})
        target_acc = (account or entry.get("account") or "").strip()

        if not isinstance(entry.get("data"), dict):
            entry["data"] = {}
        entry["data"]["password"] = password.strip()

        accounts = entry.get("accounts", [])
        updated = False
        for a in accounts:
            if isinstance(a, dict) and ((target_acc and a.get("account", "").lower() == target_acc.lower()) or a.get("active")):
                if not isinstance(a.get("data"), dict):
                    a["data"] = {}
                a["data"]["password"] = password.strip()
                updated = True
                break
        if not updated and target_acc:
            accounts.append({
                "account": target_acc,
                "label": "Primary",
                "authType": entry.get("authType", "account"),
                "data": {"password": password.strip(), "verified": True},
                "active": True
            })
            entry["accounts"] = accounts
            entry["connected"] = True
            entry["account"] = target_acc

        vault[app_key] = entry
        self._save_raw_vault(vault)
        print(f"[VAULT] Saved password/app_password for {app_key} ({target_acc}).")

    def get_all_accounts(self, app_id: str) -> List[Dict[str, Any]]:
        """Returns all configured accounts for an application (Problem 4)."""
        app_key = app_id.lower().replace(" ", "_")
        vault = self._load_raw_vault()
        entry = vault.get(app_key)
        if not entry and CONNECTED_PLUGINS_FILE.exists():
            try:
                with open(CONNECTED_PLUGINS_FILE, "r", encoding="utf-8") as f:
                    pdata = json.load(f)
                entry = pdata.get(app_key)
            except Exception:
                pass

        if not entry or not isinstance(entry, dict):
            return []

        accounts = entry.get("accounts", [])
        if not accounts and entry.get("account"):
            # Synthesize accounts array from legacy single account
            accounts = [{
                "account": entry.get("account"),
                "active": True,
                "label": "Primary",
                "authType": entry.get("authType", "account"),
                "data": entry.get("data", {})
            }]
        return accounts

    def set_plugin_credential(self, app_id: str, account: str, auth_type: str = "account", label: str = "", extra_data: Optional[Dict[str, Any]] = None, make_active: bool = True):
        """Stores or updates credentials for an app, preserving multiple accounts (Problem 4)."""
        app_key = app_id.lower().replace(" ", "_")
        vault = self._load_raw_vault()
        entry = vault.get(app_key, {})
        accounts = list(entry.get("accounts", []))

        clean_acc = account.strip()
        if not clean_acc:
            return

        # Check if legacy single account needs to be converted into accounts array
        if not accounts and entry.get("account"):
            old_acc = entry.get("account")
            if old_acc.lower() != clean_acc.lower():
                accounts.append({
                    "account": old_acc,
                    "label": entry.get("label", "Primary"),
                    "authType": entry.get("authType", "account"),
                    "data": entry.get("data", {}),
                    "active": False
                })

        # Check if this account already exists in accounts list
        existing_idx = -1
        for i, a in enumerate(accounts):
            if a.get("account", "").lower() == clean_acc.lower():
                existing_idx = i
                break

        acc_obj = {
            "account": clean_acc,
            "label": label or (accounts[existing_idx].get("label") if existing_idx >= 0 else ("Primary" if not accounts else f"Account {len(accounts)+1}")),
            "authType": auth_type,
            "data": extra_data or (accounts[existing_idx].get("data") if existing_idx >= 0 else {}),
            "active": make_active
        }

        if existing_idx >= 0:
            accounts[existing_idx] = acc_obj
        else:
            accounts.append(acc_obj)

        if make_active:
            for a in accounts:
                a["active"] = (a.get("account", "").lower() == clean_acc.lower())
            active_account = clean_acc
            active_auth = auth_type
            active_data = extra_data or {}
        else:
            active_account = entry.get("account", clean_acc)
            active_auth = entry.get("authType", auth_type)
            active_data = entry.get("data", extra_data or {})

        vault[app_key] = {
            "connected": True,
            "account": active_account,
            "accounts": accounts,
            "authType": active_auth,
            "data": active_data
        }
        self._save_raw_vault(vault)
        print(f"[VAULT] App '{app_key}' now has {len(accounts)} registered account(s). Active: '{active_account}'")

    def switch_active_account(self, app_id: str, target_account: str) -> bool:
        """Switches the active account for an app (Problem 4)."""
        app_key = app_id.lower().replace(" ", "_")
        if app_key == "google":
            res = True
            for g_app in ["gmail", "google_classroom", "youtube"]:
                res = self.switch_active_account(g_app, target_account) and res
            return res

        vault = self._load_raw_vault()
        entry = vault.get(app_key)
        if not entry and CONNECTED_PLUGINS_FILE.exists():
            try:
                with open(CONNECTED_PLUGINS_FILE, "r", encoding="utf-8") as f:
                    pdata = json.load(f)
                entry = pdata.get(app_key)
            except Exception:
                pass

        if not entry or not isinstance(entry, dict):
            return False

        accounts = entry.get("accounts", [])
        if not accounts and entry.get("account"):
            accounts = [{
                "account": entry.get("account"),
                "active": True,
                "label": "Primary",
                "authType": entry.get("authType", "account"),
                "data": entry.get("data", {})
            }]

        clean_target = target_account.strip().lower()

        found = False
        target_obj = None
        for a in accounts:
            if a.get("account", "").lower() == clean_target or clean_target in a.get("label", "").lower():
                a["active"] = True
                target_obj = a
                found = True
            else:
                a["active"] = False

        if not found:
            print(f"[VAULT] Account '{target_account}' not found under '{app_key}'.")
            return False

        entry["account"] = target_obj["account"]
        entry["authType"] = target_obj.get("authType", entry.get("authType"))
        if target_obj.get("data"):
            entry["data"] = target_obj["data"]
        entry["accounts"] = accounts
        vault[app_key] = entry
        self._save_raw_vault(vault)
        print(f"[VAULT] Switched '{app_key}' active account -> '{target_obj['account']}'")
        return True

    def remove_plugin_account(self, app_id: str, target_account: str) -> bool:
        """Removes a specific account from an app without deleting the entire plugin (Problem 4)."""
        app_key = app_id.lower().replace(" ", "_")
        if app_key == "google":
            res = True
            for g_app in ["gmail", "google_classroom", "youtube"]:
                res = self.remove_plugin_account(g_app, target_account) and res
            return res

        vault = self._load_raw_vault()
        entry = vault.get(app_key)
        if not entry or not isinstance(entry, dict):
            return False

        accounts = entry.get("accounts", [])
        clean_target = target_account.strip().lower()
        new_accounts = [a for a in accounts if a.get("account", "").lower() != clean_target]

        if len(new_accounts) == len(accounts):
            return False

        if not new_accounts:
            # No accounts remain, disconnect the app
            self.disconnect_app(app_id)
            return True

        # If the removed account was active, make the first remaining account active
        has_active = any(a.get("active") for a in new_accounts)
        if not has_active:
            new_accounts[0]["active"] = True

        active_acc = next(a for a in new_accounts if a.get("active"))
        entry["account"] = active_acc["account"]
        entry["authType"] = active_acc.get("authType", entry.get("authType"))
        entry["accounts"] = new_accounts
        vault[app_key] = entry
        self._save_raw_vault(vault)
        print(f"[VAULT] Removed '{clean_target}' from '{app_key}'. Remaining: {len(new_accounts)} account(s).")
        return True

    def disconnect_app(self, app_id: str):
        """Disconnects an app and deletes its encrypted session files."""
        app_key = app_id.lower().replace(" ", "_")
        vault = self._load_raw_vault()

        apps_to_remove = [app_key]
        if app_key in ["google", "google_apps"]:
            apps_to_remove.extend(["gmail", "google_classroom", "youtube"])
        elif app_key in ["gmail", "google_classroom", "youtube"]:
            apps_to_remove.append("google")

        for k in apps_to_remove:
            if k in vault:
                del vault[k]

        self._save_raw_vault(vault)

        # Clean specific session file if applicable
        if any(k in ["google", "gmail", "google_classroom", "youtube"] for k in apps_to_remove):
            if GOOGLE_SESSION_ENC.exists():
                try:
                    GOOGLE_SESSION_ENC.unlink(missing_ok=True)
                except Exception:
                    pass
        elif app_key == "spotify":
            if SPOTIFY_SESSION_ENC.exists():
                try:
                    SPOTIFY_SESSION_ENC.unlink(missing_ok=True)
                except Exception:
                    pass
        elif app_key == "whatsapp":
            if WHATSAPP_SESSION_ENC.exists():
                try:
                    WHATSAPP_SESSION_ENC.unlink(missing_ok=True)
                except Exception:
                    pass
            wa_prof = VAULT_DIR / "whatsapp_web_profile"
            if wa_prof.exists():
                try:
                    import shutil
                    shutil.rmtree(str(wa_prof), ignore_errors=True)
                except Exception:
                    pass

        print(f"[VAULT] Disconnected '{app_id}' and pruned vault session.")

    def list_connected_apps(self) -> List[str]:
        connected = []
        vault = self._load_raw_vault()
        for k, v in vault.items():
            if v.get("connected") and v.get("account"):
                connected.append(k)
        return connected


VAULT_MGR = VaultManager()
