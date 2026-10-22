"""
HYDRA Entry Point Forwarder.
Runs HYDRA System Interface from PersonalJarvis-main.
"""
import os
import sys
import runpy

def main():
    inner_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "PersonalJarvis-main")
    if os.path.isdir(inner_dir):
        os.chdir(inner_dir)
        sys.path.insert(0, inner_dir)
        inner_main_file = os.path.join(inner_dir, "main.py")
        runpy.run_path(inner_main_file, run_name="__main__")
    else:
        print("[ERROR] Could not find inner PersonalJarvis-main directory.")

if __name__ == "__main__":
    main()
