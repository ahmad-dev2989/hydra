import subprocess
import os
import psutil
import difflib
import shutil

class SystemDriver:
    def open_application(self, name):
        try:
            subprocess.Popen(f"cmd /c start {name}", shell=True)
            return True, f"Launched {name}"
        except Exception as e:
            return False, str(e)

    def close_application(self, name):
        try:
            clean_name = name if name.lower().endswith(".exe") else f"{name}.exe"
            subprocess.run(["taskkill", "/f", "/im", clean_name], capture_output=True)
            return True, f"Closed {name}"
        except Exception as e:
            return False, str(e)

    def list_running_apps(self):
        apps = []
        for proc in psutil.process_iter(['name']):
            try:
                name = proc.info.get('name')
                if name:
                    apps.append(name)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return list(set(apps))

    def shutdown_pc(self):
        subprocess.run("shutdown /s /t 1", shell=True)
        return True, "Shutting down"

    def restart_pc(self):
        subprocess.run("shutdown /r /t 1", shell=True)
        return True, "Restarting"

    def lock_pc(self):
        subprocess.run("rundll32.exe user32.dll,LockWorkStation", shell=True)
        return True, "Locked"

    # --- NETWORK CONTROL ---
    def toggle_wifi(self, enable=True):
        state = "ENABLED" if enable else "DISABLED"
        try:
            cmd = f'netsh interface set interface name="Wi-Fi" admin={state}'
            subprocess.run(cmd, shell=True, capture_output=True)
            return True, f"Wi-Fi adapter {state.lower()}"
        except Exception as e:
            return False, str(e)

    def connect_to_wifi(self, ssid):
        if not ssid:
            return False, "No network SSID specified."
        try:
            cmd = f'netsh wlan connect name="{ssid}"'
            subprocess.run(cmd, shell=True, capture_output=True, text=True)
            return True, ssid
        except Exception as e:
            return False, str(e)

    # --- SMART FILESYSTEM ENGINE ---
    def _get_search_roots(self):
        home = os.path.expanduser("~")
        return [
            os.path.join(home, "Desktop"),
            os.path.join(home, "Downloads"),
            os.path.join(home, "Documents"),
            os.path.join(home, "Pictures"),
            os.path.join(home, "Videos")
        ]

    def locate_path(self, target_name, location_limit=None, target_type="ANY"):
        clean = target_name.lower().strip()
        search_roots = self._get_search_roots()

        if location_limit:
            resolved_parent = self.resolve_directory(location_limit)
            if os.path.exists(resolved_parent):
                search_roots = [resolved_parent]

        for root_dir in search_roots:
            if not os.path.exists(root_dir):
                continue
            for root, dirs, files in os.walk(root_dir):
                if target_type in ["FOLDER", "ANY"]:
                    for d in dirs:
                        if clean == d.lower():
                            return True, os.path.join(root, d), True
                if target_type in ["FILE", "ANY"]:
                    for f in files:
                        name_no_ext = os.path.splitext(f.lower())[0]
                        if clean == f.lower() or clean == name_no_ext:
                            return True, os.path.join(root, f), False
        return False, "", False

    def locate_all_paths(self, target_name, target_type="ANY"):
        clean = target_name.lower().strip()
        search_roots = self._get_search_roots()
        matches = []

        for root_dir in search_roots:
            if not os.path.exists(root_dir):
                continue
            for root, dirs, files in os.walk(root_dir):
                if target_type in ["FOLDER", "ANY"]:
                    for d in dirs:
                        if clean == d.lower():
                            matches.append(os.path.join(root, d))
                if target_type in ["FILE", "ANY"]:
                    for f in files:
                        name_no_ext = os.path.splitext(f.lower())[0]
                        if clean == f.lower() or clean == name_no_ext:
                            matches.append(os.path.join(root, f))
        return matches

    def resolve_directory(self, location_name):
        home = os.path.expanduser("~")
        if not location_name:
            return os.path.join(home, "Desktop")

        low = location_name.lower().strip()
        shortcuts = {
            "desktop": os.path.join(home, "Desktop"),
            "downloads": os.path.join(home, "Downloads"),
            "documents": os.path.join(home, "Documents"),
            "pictures": os.path.join(home, "Pictures"),
            "videos": os.path.join(home, "Videos"),
        }
        if low in shortcuts:
            return shortcuts[low]

        # 1. Try exact folder search
        found, path, is_dir = self.locate_path(location_name, target_type="FOLDER")
        if found and is_dir:
            return path

        # 2. Smart NLP fallback
        words = [w for w in low.split() if w not in ["in", "inside", "at", "the", "folder", "directory"]]
        for w in words:
            if w in shortcuts:
                return shortcuts[w]
            found, path, is_dir = self.locate_path(w, target_type="FOLDER")
            if found and is_dir:
                return path

        return os.path.join(home, "Desktop")

    def smart_create(self, item_type, name, location):
        dest_dir = self.resolve_directory(location)
        full_path = os.path.join(dest_dir, name)
        try:
            if item_type == "FOLDER":
                os.makedirs(full_path, exist_ok=True)
                return True, f"Folder '{name}' created in {os.path.basename(dest_dir)}."
            else:
                templates = {
                    ".py": "# Created by HYDRA\n\n",
                    ".html": "<!DOCTYPE html>\n<html>\n<head>\n\t<title>Document</title>\n</head>\n<body>\n\n</body>\n</html>",
                    ".js": "// Generated by HYDRA\n",
                    ".json": "{\n\n}"
                }
                _, ext = os.path.splitext(name)
                content = templates.get(ext.lower(), "")

                with open(full_path, 'w', encoding='utf-8') as f:
                    f.write(content)

                return True, f"File '{name}' created in {os.path.basename(dest_dir)}."
        except Exception as e:
            return False, str(e)

    def smart_delete(self, target_name):
        found, path, is_dir = self.locate_path(target_name)
        if not found:
            return False, f"Could not locate '{target_name}' to delete."

        home = os.path.expanduser("~")
        if not os.path.abspath(path).startswith(os.path.abspath(home)):
            return False, "Safety Valve: Refusing to delete OS files outside your personal User directory."

        try:
            # Use safe recycle bin if send2trash is available, else standard delete
            try:
                import send2trash
                send2trash.send2trash(path)
            except ImportError:
                if is_dir:
                    shutil.rmtree(path)
                else:
                    os.remove(path)
            return True, f"Purged {os.path.basename(path)}, sir."
        except Exception as e:
            return False, f"Deletion error: {e}"

    def smart_move(self, source_name, dest_location):
        found, src_path, _ = self.locate_path(source_name)
        if not found:
            return False, f"Source item '{source_name}' not found."

        dest_dir = self.resolve_directory(dest_location)
        try:
            shutil.move(src_path, os.path.join(dest_dir, os.path.basename(src_path)))
            return True, f"Moved '{source_name}' into {os.path.basename(dest_dir)}."
        except Exception as e:
            return False, f"Move error: {e}"

    def change_extension(self, file_name_or_path, new_ext):
        if os.path.isabs(file_name_or_path) and os.path.exists(file_name_or_path):
            path = file_name_or_path
            is_dir = os.path.isdir(path)
            found = True
        else:
            found, path, is_dir = self.locate_path(file_name_or_path)

        if not found or is_dir:
            return False, "Could not find file to modify."

        new_ext = new_ext.replace("dot ", ".").strip()
        if not new_ext.startswith('.'):
            new_ext = '.' + new_ext

        base = os.path.splitext(path)[0]
        new_path = base + new_ext
        try:
            os.rename(path, new_path)
            return True, f"Changed type of {os.path.basename(path)} to {new_ext}."
        except Exception as e:
            return False, f"Failed to change extension: {e}"

    def execute_command(self, action, payload=None):
        if action == "OPEN_APP":
            return self.open_application(payload)
        if action == "CLOSE_APP":
            return self.close_application(payload)
        if action == "LIST_APPS":
            return True, ", ".join(self.list_running_apps()[:10])
        if action == "SHUTDOWN":
            return self.shutdown_pc()
        if action == "RESTART":
            return self.restart_pc()
        if action == "LOCK":
            return self.lock_pc()
        if action == "WIFI_ON":
            return self.toggle_wifi(enable=True)
        if action == "WIFI_OFF":
            return self.toggle_wifi(enable=False)
        if action == "WIFI_CONNECT":
            return self.connect_to_wifi(payload)
        return False, "UNKNOWN_ACTION"