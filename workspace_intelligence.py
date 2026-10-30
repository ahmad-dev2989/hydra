"""
HYDRA Phase 13: Workspace Intelligence & File Operations Engine
Implements:
1. Universal File Locator:
   - Dynamically resolves files across Desktop, Documents, Downloads, Pictures, and user subdirectories.
2. File Launch & Discovery:
   - 'open <filename>' -> Opens file with default native Windows app.
   - 'find file <filename>' -> Locates file and highlights it directly in File Explorer.
3. Multi-Format Conversions:
   - PDF to PNG/JPG image rendering (PyMuPDF).
   - Images to compiled PDF (Pillow / PyMuPDF).
   - CSV to Microsoft Excel (.xlsx) formatting (openpyxl).
   - Word (.docx) to PDF conversion (native Word COM automation).
   - Image format transcoding (PNG, JPG, WEBP, BMP, ICO).
4. Deep Content Search & Directory Organization.
"""
import os
import re
import csv
import subprocess
from typing import Tuple, List, Dict, Any, Optional
from PIL import Image

def locate_file_on_laptop(filename: str) -> Optional[str]:
    """
    Dynamically resolves a file by name across the user's laptop:
    Checks priority folders (Documents, sgs, Desktop, Downloads, Pictures).
    Returns absolute path if found, or None.
    """
    clean = filename.strip('\'" ')
    if not clean:
        return None

    if os.path.isabs(clean) and os.path.exists(clean):
        return clean

    if os.path.exists(clean):
        return os.path.abspath(clean)

    user_home = os.path.expanduser('~')
    priority_dirs = [
        os.path.join(user_home, 'Documents', 'sgs'),
        os.path.join(user_home, 'Desktop'),
        os.path.join(user_home, 'Downloads'),
        os.path.join(user_home, 'Documents'),
        os.path.join(user_home, 'Pictures'),
        os.getcwd()
    ]

    clean_lower = clean.lower()

    # Step 1: Fast direct checks in priority roots
    for pdir in priority_dirs:
        direct = os.path.join(pdir, clean)
        if os.path.exists(direct):
            return direct

    # Step 2: Shallow recursive walk in priority directories
    for pdir in priority_dirs:
        if os.path.exists(pdir):
            for root, dirs, files in os.walk(pdir):
                # Skip hidden folders, git, node_modules, virtualenvs
                dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['node_modules', '__pycache__', '.venv', 'venv']]
                for f in files:
                    if f.lower() == clean_lower:
                        return os.path.join(root, f)
                    if not os.path.splitext(clean_lower)[1] and os.path.splitext(f.lower())[0] == clean_lower:
                        return os.path.join(root, f)

    return None


class WorkspaceIntelligenceEngine:
    """
    Core executor for file operations, conversions, content searching, and folder intelligence.
    """
    def __init__(self, sys_driver: Any = None):
        self.sys_driver = sys_driver

    def _is_mock(self) -> bool:
        return getattr(self.sys_driver, '_mock_mode', False)

    # =========================================================================
    # 1. FILE LOCATE & LAUNCH (OPEN / FIND)
    # =========================================================================
    def open_file_on_laptop(self, filename: str) -> Tuple[bool, str]:
        """Finds and opens a file on the user's laptop with its default Windows application."""
        resolved = locate_file_on_laptop(filename)
        if not resolved:
            return False, f"Sir, I could not find '{filename}' on your laptop."

        print(f"[WORKSPACE] Opening file '{resolved}'...")
        if self._is_mock():
            return True, f"Opening '{os.path.basename(resolved)}', sir."

        try:
            os.startfile(resolved)
            folder_name = os.path.basename(os.path.dirname(resolved))
            return True, f"Opening '{os.path.basename(resolved)}' from your {folder_name} folder, sir."
        except Exception as e:
            return False, f"Error opening '{filename}': {e}"

    def find_file_on_laptop(self, filename: str) -> Tuple[bool, str]:
        """Locates a file and opens its containing folder in File Explorer with the file selected."""
        resolved = locate_file_on_laptop(filename)
        if not resolved:
            return False, f"Sir, I searched your laptop folders but could not find '{filename}'."

        print(f"[WORKSPACE] Found file at '{resolved}'. Highlighting in File Explorer...")
        if self._is_mock():
            return True, f"Found '{os.path.basename(resolved)}' at '{resolved}', sir."

        try:
            subprocess.Popen(f'explorer.exe /select,"{resolved}"')
            folder_name = os.path.basename(os.path.dirname(resolved))
            return True, f"Found '{os.path.basename(resolved)}' in your {folder_name} folder, sir. I have opened its location for you."
        except Exception as e:
            return True, f"Found '{os.path.basename(resolved)}' at '{resolved}', sir."

    # =========================================================================
    # 2. PDF TO IMAGES CONVERSION
    # =========================================================================
    def convert_pdf_to_images(self, pdf_path: str, output_dir: Optional[str] = None, fmt: str = "png", dpi: int = 150) -> Tuple[bool, str, List[str]]:
        """Renders every page of a PDF document as high-resolution PNG or JPG images."""
        resolved_pdf = locate_file_on_laptop(pdf_path)
        if not resolved_pdf or not os.path.exists(resolved_pdf):
            return False, f"PDF file not found: '{pdf_path}'", []

        if not output_dir:
            base_dir = os.path.dirname(os.path.abspath(resolved_pdf))
            stem = os.path.splitext(os.path.basename(resolved_pdf))[0]
            output_dir = os.path.join(base_dir, f"{stem}_images")

        os.makedirs(output_dir, exist_ok=True)
        generated_files = []

        try:
            import fitz  # PyMuPDF
            doc = fitz.open(resolved_pdf)
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                pix = page.get_pixmap(dpi=dpi)
                out_name = f"page_{page_num + 1}.{fmt.lower()}"
                out_path = os.path.join(output_dir, out_name)
                pix.save(out_path)
                generated_files.append(out_path)
            doc.close()

            reply = f"Successfully converted {len(generated_files)} pages of '{os.path.basename(resolved_pdf)}' to {fmt.upper()} images in '{output_dir}', sir."
            return True, reply, generated_files
        except Exception as e:
            return False, f"PDF to Image conversion error: {e}", []

    # =========================================================================
    # 3. IMAGES TO PDF COMPILATION
    # =========================================================================
    def convert_images_to_pdf(self, image_paths: List[str], output_pdf: str) -> Tuple[bool, str]:
        """Compiles multiple image files into a single unified PDF document."""
        resolved_images = []
        for p in image_paths:
            res = locate_file_on_laptop(p)
            if res and os.path.exists(res):
                resolved_images.append(res)

        if not resolved_images:
            return False, "No valid image files provided."

        try:
            pil_images = []
            first_image = None

            for p in resolved_images:
                im = Image.open(p)
                if im.mode != "RGB":
                    im = im.convert("RGB")
                if first_image is None:
                    first_image = im
                else:
                    pil_images.append(im)

            os.makedirs(os.path.dirname(os.path.abspath(output_pdf)), exist_ok=True)
            first_image.save(output_pdf, "PDF", resolution=100.0, save_all=True, append_images=pil_images)

            return True, f"Successfully compiled {len(resolved_images)} images into '{output_pdf}', sir."
        except Exception as e:
            return False, f"Image to PDF conversion error: {e}"

    # =========================================================================
    # 4. CSV TO EXCEL CONVERSION
    # =========================================================================
    def convert_csv_to_excel(self, csv_path: str, output_xlsx: Optional[str] = None) -> Tuple[bool, str]:
        """Converts a CSV file into a formatted Microsoft Excel .xlsx spreadsheet."""
        resolved_csv = locate_file_on_laptop(csv_path)
        if not resolved_csv or not os.path.exists(resolved_csv):
            return False, f"CSV file not found: '{csv_path}'"

        if not output_xlsx:
            output_xlsx = os.path.splitext(resolved_csv)[0] + ".xlsx"

        try:
            import openpyxl
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = os.path.splitext(os.path.basename(resolved_csv))[0][:30]

            with open(resolved_csv, "r", encoding="utf-8-sig", errors="ignore") as f:
                reader = csv.reader(f)
                for row_idx, row in enumerate(reader, 1):
                    for col_idx, val in enumerate(row, 1):
                        try:
                            if "." in val:
                                num = float(val)
                            else:
                                num = int(val)
                            ws.cell(row=row_idx, column=col_idx, value=num)
                        except ValueError:
                            ws.cell(row=row_idx, column=col_idx, value=val)

            wb.save(output_xlsx)
            return True, f"Successfully converted '{os.path.basename(resolved_csv)}' to Excel workbook at '{output_xlsx}', sir."
        except Exception as e:
            return False, f"CSV to Excel conversion error: {e}"

    # =========================================================================
    # 5. WORD (.DOCX) TO PDF CONVERSION
    # =========================================================================
    def convert_docx_to_pdf(self, docx_path: str, output_pdf: Optional[str] = None) -> Tuple[bool, str]:
        """Converts Word document to PDF using native Windows Word COM automation."""
        resolved_docx = locate_file_on_laptop(docx_path)
        if not resolved_docx or not os.path.exists(resolved_docx):
            return False, f"Word document not found: '{docx_path}'"

        if not output_pdf:
            output_pdf = os.path.splitext(resolved_docx)[0] + ".pdf"

        abs_docx = os.path.abspath(resolved_docx)
        abs_pdf = os.path.abspath(output_pdf)

        try:
            import win32com.client
            import pythoncom
            pythoncom.CoInitialize()
            word = win32com.client.DispatchEx("Word.Application")
            word.Visible = False
            doc = word.Documents.Open(abs_docx)
            doc.SaveAs(abs_pdf, FileFormat=17)  # 17 = wdExportFormatPDF
            doc.Close()
            word.Quit()
            pythoncom.CoUninitialize()

            return True, f"Successfully converted Word document to '{abs_pdf}', sir."
        except Exception as e:
            return False, f"Word to PDF conversion error: {e}"

    # =========================================================================
    # 6. IMAGE FORMAT TRANSCODING (PNG, JPG, WEBP, BMP, ICO)
    # =========================================================================
    def convert_image_format(self, image_path: str, target_format: str, output_path: Optional[str] = None) -> Tuple[bool, str]:
        """Converts an image file between formats (PNG, JPG, WEBP, BMP, ICO)."""
        resolved_img = locate_file_on_laptop(image_path)
        if not resolved_img or not os.path.exists(resolved_img):
            return False, f"Image not found: '{image_path}'"

        tgt_ext = target_format.lower().lstrip('.')
        if tgt_ext == "jpeg":
            tgt_ext = "jpg"

        if not output_path:
            output_path = os.path.splitext(resolved_img)[0] + f".{tgt_ext}"

        try:
            im = Image.open(resolved_img)
            if tgt_ext in ["jpg", "jpeg"] and im.mode in ["RGBA", "P"]:
                im = im.convert("RGB")
            im.save(output_path)
            return True, f"Successfully converted '{os.path.basename(resolved_img)}' to {tgt_ext.upper()} at '{output_path}', sir."
        except Exception as e:
            return False, f"Image transcoding error: {e}"

    # =========================================================================
    # 7. DEEP FILE CONTENT SEARCH
    # =========================================================================
    def search_files_by_content(self, query: str, search_dir: Optional[str] = None, max_results: int = 15) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """Scans text files, Python scripts, JSON, CSV, and PDFs for occurrences of query."""
        if not search_dir:
            search_dir = os.getcwd()

        clean_q = query.strip().lower()
        results = []
        supported_text_exts = {'.txt', '.py', '.json', '.csv', '.md', '.html', '.css', '.js', '.yaml', '.yml', '.env'}

        try:
            for root, dirs, files in os.walk(search_dir):
                dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['node_modules', '__pycache__', 'venv', '.venv']]
                for fname in files:
                    ext = os.path.splitext(fname)[1].lower()
                    fpath = os.path.join(root, fname)

                    if ext in supported_text_exts:
                        try:
                            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                                for line_no, line in enumerate(f, 1):
                                    if clean_q in line.lower():
                                        results.append({
                                            "file": fpath,
                                            "filename": fname,
                                            "line": line_no,
                                            "snippet": line.strip()[:140]
                                        })
                                        if len(results) >= max_results:
                                            break
                        except Exception:
                            pass
                    elif ext == '.pdf':
                        try:
                            import fitz
                            doc = fitz.open(fpath)
                            for page_no, page in enumerate(doc, 1):
                                text = page.get_text()
                                if clean_q in text.lower():
                                    results.append({
                                        "file": fpath,
                                        "filename": fname,
                                        "page": page_no,
                                        "snippet": f"Found on page {page_no}"
                                    })
                                    if len(results) >= max_results:
                                        break
                            doc.close()
                        except Exception:
                            pass

                    if len(results) >= max_results:
                        break
                if len(results) >= max_results:
                    break

            if results:
                reply = f"Found {len(results)} matches for '{query}' across files in '{os.path.basename(search_dir)}', sir."
                return True, reply, results
            else:
                return False, f"No files containing '{query}' were found.", []
        except Exception as e:
            return False, f"File search error: {e}", []

    # =========================================================================
    # 8. DIRECTORY ORGANIZATION
    # =========================================================================
    def organize_directory(self, target_dir: str) -> Tuple[bool, str]:
        """Categorizes and sorts files in a cluttered directory into categorized folders."""
        if not os.path.exists(target_dir):
            return False, f"Directory does not exist: '{target_dir}'"

        CATEGORIES = {
            "Images": {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".ico"},
            "Documents": {".pdf", ".docx", ".doc", ".txt", ".rtf", ".pptx", ".ppt"},
            "Spreadsheets": {".xlsx", ".xls", ".csv", ".tsv"},
            "Archives": {".zip", ".rar", ".7z", ".tar", ".gz"},
            "Audio": {".mp3", ".wav", ".aac", ".flac", ".ogg", ".m4a"},
            "Videos": {".mp4", ".mkv", ".mov", ".avi", ".webm"},
            "Code": {".py", ".js", ".html", ".css", ".json", ".xml", ".ts", ".c", ".cpp"}
        }

        moved_count = 0
        try:
            for item in os.listdir(target_dir):
                item_path = os.path.join(target_dir, item)
                if os.path.isfile(item_path):
                    ext = os.path.splitext(item)[1].lower()
                    target_folder = None
                    for cat_name, cat_exts in CATEGORIES.items():
                        if ext in cat_exts:
                            target_folder = cat_name
                            break

                    if target_folder:
                        dest_dir = os.path.join(target_dir, target_folder)
                        os.makedirs(dest_dir, exist_ok=True)
                        dest_path = os.path.join(dest_dir, item)
                        os.rename(item_path, dest_path)
                        moved_count += 1

            return True, f"Organized {moved_count} files in '{target_dir}' into categorized folders, sir."
        except Exception as e:
            return False, f"Directory organization error: {e}"


# =============================================================================
# 9. DISPATCHER FOR WORKSPACE & FILE OPERATIONS
# =============================================================================

WORKSPACE_ENGINE = WorkspaceIntelligenceEngine()

def handle_workspace_intelligence(command: str, sys_driver: Any = None) -> Tuple[bool, str]:
    """
    Main entry point for Phase 13 Workspace Intelligence:
    1. Open files ('open day 4.png')
    2. Find files ('find file day 4.png', 'find day 6.png')
    3. Convert files ('convert day 6.png to jpg', 'convert file.pdf to png')
    4. Content search & folder organization
    """
    clean = command.strip().lower().rstrip('.!? ')
    engine = WorkspaceIntelligenceEngine(sys_driver)

    # -------------------------------------------------------------------------
    # A. OPEN FILE COMMANDS ('open day 4.png', 'open file cv.png')
    # -------------------------------------------------------------------------
    m_open = re.search(r'^(?:open|launch|show)\s+(?:file\s+)?[\'"]?([^\'"]+?\.(?:png|jpe?g|webp|bmp|pdf|docx?|xlsx?|txt|csv|py|zip|mp4|mp3))[\'"]?$', clean)
    if m_open:
        target_file = m_open.group(1).strip()
        ok, msg = engine.open_file_on_laptop(target_file)
        return True, msg

    # Also handle 'open file <name without extension>'
    m_open_file_generic = re.search(r'^(?:open|launch)\s+file\s+[\'"]?([^\'"]+?)[\'"]?$', clean)
    if m_open_file_generic:
        target_file = m_open_file_generic.group(1).strip()
        ok, msg = engine.open_file_on_laptop(target_file)
        return True, msg

    # -------------------------------------------------------------------------
    # B. FIND FILE COMMANDS ('find file day 4.png', 'search file day 6.png', 'find day 4.png')
    # -------------------------------------------------------------------------
    m_find = re.search(r'^(?:find|search|locate)\s+(?:file\s+)?[\'"]?([^\'"]+?\.(?:png|jpe?g|webp|bmp|pdf|docx?|xlsx?|txt|csv|py|zip|mp4|mp3))[\'"]?$', clean)
    if m_find:
        target_file = m_find.group(1).strip()
        ok, msg = engine.find_file_on_laptop(target_file)
        return True, msg

    # Also handle 'find file <name without extension>'
    m_find_file_generic = re.search(r'^(?:find|search|locate)\s+file\s+[\'"]?([^\'"]+?)[\'"]?$', clean)
    if m_find_file_generic:
        target_file = m_find_file_generic.group(1).strip()
        ok, msg = engine.find_file_on_laptop(target_file)
        return True, msg

    # -------------------------------------------------------------------------
    # C. FILE CONVERSIONS
    # -------------------------------------------------------------------------
    # 1. Image Transcoding ('convert day 6.png to jpg')
    m_img = re.search(r'convert\s+[\'"]?([^\'"]+?\.(?:png|jpe?g|webp|bmp))[\'"]?\s+(?:to|into)\s+(png|jpe?g|webp|bmp|ico)', clean)
    if m_img:
        img_file, tgt_fmt = m_img.group(1).strip(), m_img.group(2).strip()
        ok, msg = engine.convert_image_format(img_file, tgt_fmt)
        return True, msg

    # 2. PDF to PNG/JPG ('convert invoice.pdf to png')
    m_pdf2img = re.search(r'convert\s+[\'"]?([^\'"]+?\.pdf)[\'"]?\s+(?:to|into)\s+(?:images?|png|jpg|jpeg)', clean)
    if m_pdf2img:
        pdf_file = m_pdf2img.group(1).strip()
        fmt = "png" if "png" in clean else "jpg" if "jpg" in clean else "png"
        ok, msg, _ = engine.convert_pdf_to_images(pdf_file, fmt=fmt)
        return True, msg

    # 3. CSV to Excel ('convert sales.csv to excel')
    m_csv2xlsx = re.search(r'convert\s+[\'"]?([^\'"]+?\.csv)[\'"]?\s+(?:to|into)\s+(?:excel|xlsx|spreadsheet)', clean)
    if m_csv2xlsx:
        csv_file = m_csv2xlsx.group(1).strip()
        ok, msg = engine.convert_csv_to_excel(csv_file)
        return True, msg

    # 4. Word to PDF ('convert report.docx to pdf')
    m_docx2pdf = re.search(r'convert\s+[\'"]?([^\'"]+?\.docx?)[\'"]?\s+(?:to|into)\s+pdf', clean)
    if m_docx2pdf:
        docx_file = m_docx2pdf.group(1).strip()
        ok, msg = engine.convert_docx_to_pdf(docx_file)
        return True, msg

    # -------------------------------------------------------------------------
    # D. CONTENT SEARCH & FOLDER CLEANUP
    # -------------------------------------------------------------------------
    m_search = re.search(r'(?:find|search)\s+(?:files?|documents?)\s+(?:containing|with|for)\s+[\'"]?([^\'"]+?)[\'"]?$', clean)
    if m_search:
        query = m_search.group(1).strip()
        ok, msg, items = engine.search_files_by_content(query)
        if ok and items:
            snippets = "\n".join([f"- {it['filename']}: {it['snippet']}" for it in items[:5]])
            return True, f"{msg}\n{snippets}"
        return ok, msg

    if any(k in clean for k in ["organize", "clean up", "sort"]) and any(d in clean for d in ["downloads", "desktop", "folder"]):
        target = os.path.join(os.path.expanduser("~"), "Downloads") if "downloads" in clean else os.path.join(os.path.expanduser("~"), "Desktop") if "desktop" in clean else os.getcwd()
        ok, msg = engine.organize_directory(target)
        return ok, msg

    return False, ""
