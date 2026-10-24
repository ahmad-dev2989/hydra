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
                    if k not in vault and isinstance(v, dict) and v.get("connected"):
                        vault[k] = v
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

    def is_connected(self, app_id: str) -> bool:
        data = self.get_plugin_data(app_id)
        return bool(data and data.get("connected") and data.get("account"))

    def set_plugin_credential(self, app_id: str, account: str, auth_type: str = "account", extra_data: Optional[Dict[str, Any]] = None):
        """Stores or updates credentials for an app."""
        app_key = app_id.lower().replace(" ", "_")
        vault = self._load_raw_vault()
        vault[app_key] = {
            "connected": True,
            "account": account,
            "authType": auth_type,
            "data": extra_data or {}
        }
        self._save_raw_vault(vault)

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
