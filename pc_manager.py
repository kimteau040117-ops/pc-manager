import sys
import ctypes
try:
    import win32com.client
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

if not is_admin():
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, " ".join(sys.argv), None, 1
    )
    sys.exit()

import os
import winreg
import subprocess
import hashlib
from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QTableWidget, QTableWidgetItem,
    QLabel, QHeaderView, QMessageBox, QComboBox, QTabWidget,
    QProgressBar, QFileDialog, QMenu
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QIcon, QPixmap


# ─────────────────────────────────────────────
# 상수
# ─────────────────────────────────────────────

# 불필요한 보안/번들 앱 키워드 (이름에 포함되면 경고 표시)
BLOATWARE_KEYWORDS = [
    "mcafee", "norton", "avast", "avg", "kaspersky",
    "naver vaccine", "v3", "ahnlab", "알약", "바이로봇",
    "olleh", "kt", "skt", "lg u+", "sysinternals",
    "toolbar", "조각 모음", "pc optimizer", "cleaner",
    "speedup", "booster", "driver updater",
]

# 중복 파일 스캔 대상 폴더 & 확장자
SCAN_FOLDERS = [
    os.path.expanduser("~/Documents"),
    os.path.expanduser("~/Downloads"),
    os.path.expanduser("~/Desktop"),
    os.path.expanduser("~/Pictures"),
]
SCAN_EXTENSIONS = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx",
                   ".ppt", ".txt", ".zip",
                   ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
EXE_EXTENSIONS  = {".exe", ".msi"}  # 실행파일은 별도 표시




# ─────────────────────────────────────────────
# 프로그램 아이콘 추출
# ─────────────────────────────────────────────
def extract_icon_from_exe(exe_path):
    """pywin32로 .exe에서 아이콘 추출 후 QIcon 반환"""
    if not HAS_WIN32 or not exe_path or not os.path.exists(exe_path):
        return None
    try:
        import win32gui
        import win32con
        import win32ui
        import struct

        # 아이콘 핸들 추출
        large, small = win32gui.ExtractIconEx(exe_path, 0, 1)
        if not large:
            return None
        hicon = large[0]

        # 아이콘 → HBITMAP 변환
        hdc      = win32ui.CreateDCFromHandle(win32gui.GetDC(0))
        hdc_mem  = hdc.CreateCompatibleDC()
        bmp      = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(hdc, 32, 32)
        hdc_mem.SelectObject(bmp)
        hdc_mem.FillSolidRect((0, 0, 32, 32), 0x00FFFFFF)
        win32gui.DrawIconEx(hdc_mem.GetSafeHdc(), 0, 0, hicon, 32, 32, 0, None, win32con.DI_NORMAL)

        # HBITMAP → QPixmap
        bmp_info = bmp.GetInfo()
        bmp_bits = bmp.GetBitmapBits(True)
        img = QPixmap()
        img.loadFromData(
            struct.pack("IIIHHIIIIII",
                40, bmp_info["bmWidth"], -bmp_info["bmHeight"], 1,
                bmp_info["bmBitsPixel"], 0,
                len(bmp_bits), 0, 0, 0, 0
            ) + bmp_bits
        )

        win32gui.DestroyIcon(hicon)
        if not img.isNull():
            return QIcon(img)
    except Exception:
        pass
    return None


def get_program_icon(icon_path_raw, install_loc):
    """레지스트리 DisplayIcon 또는 설치 경로에서 아이콘 추출"""
    candidates = []

    # DisplayIcon 경로 파싱 (예: "C:\path\app.exe,0")
    if icon_path_raw:
        path = icon_path_raw.split(",")[0].strip().strip('"')
        candidates.append(path)

    # 설치 경로에서 .exe 찾기
    if install_loc and os.path.isdir(install_loc):
        try:
            for f in os.listdir(install_loc):
                if f.lower().endswith(".exe"):
                    candidates.append(os.path.join(install_loc, f))
                    break
        except PermissionError:
            pass

    for path in candidates:
        if not os.path.exists(path):
            continue
        # pywin32로 .exe 아이콘 직접 추출
        icon = extract_icon_from_exe(path)
        if icon:
            return icon
        # .ico, .png 파일이면 바로 로드
        if path.lower().endswith((".ico", ".png")):
            icon = QIcon(path)
            if not icon.isNull():
                return icon
    return QIcon()

# ─────────────────────────────────────────────
# 바로가기(.lnk) 원본 경로 추출
# ─────────────────────────────────────────────
def resolve_shortcut(path):
    """바로가기 파일이면 원본 경로 반환, 아니면 그대로 반환"""
    if path.lower().endswith(".lnk") and HAS_WIN32:
        try:
            shell  = win32com.client.Dispatch("WScript.Shell")
            target = shell.CreateShortCut(path).Targetpath
            return target if target else path
        except Exception:
            pass
    return path

# ─────────────────────────────────────────────
# 실행 여부 & 마지막 실행일 헬퍼
# ─────────────────────────────────────────────
def get_running_processes():
    """현재 실행 중인 프로세스 이름 집합 반환"""
    try:
        result = subprocess.run(
            ["tasklist", "/fo", "csv", "/nh"],
            capture_output=True, text=True, timeout=5
        )
        processes = set()
        for line in result.stdout.splitlines():
            parts = line.strip().strip('"').split('","')
            if parts:
                processes.add(parts[0].lower())
        return processes
    except Exception:
        return set()

def get_last_run_from_prefetch(exe_name):
    """Prefetch 폴더에서 마지막 실행일 추출"""
    prefetch = r"C:\Windows\Prefetch"
    if not os.path.isdir(prefetch):
        return "-"
    exe_upper = exe_name.upper()
    latest = None
    try:
        for f in os.listdir(prefetch):
            if f.upper().startswith(exe_upper) and f.upper().endswith(".PF"):
                fp = os.path.join(prefetch, f)
                mtime = os.path.getmtime(fp)
                if latest is None or mtime > latest:
                    latest = mtime
    except PermissionError:
        return "권한 없음"
    if latest:
        return datetime.fromtimestamp(latest).strftime("%Y-%m-%d")
    return "-"

def guess_exe_name(name, install_loc):
    """설치 경로에서 .exe 파일 이름 추측"""
    if install_loc and os.path.isdir(install_loc):
        try:
            for f in os.listdir(install_loc):
                if f.lower().endswith(".exe"):
                    return f
        except PermissionError:
            pass
    # 이름 첫 단어로 추측
    return name.split()[0].lower().replace(" ", "") + ".exe"

# ─────────────────────────────────────────────
# 1. 레지스트리 스캔
# ─────────────────────────────────────────────
REGISTRY_PATHS = [
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
]


def get_store_apps():
    """Microsoft Store 앱 목록 수집"""
    store_apps = []
    try:
        result = subprocess.run(
            ["powershell", "-Command",
             "Get-AppxPackage | Select-Object Name, Version, Publisher, InstallLocation | ConvertTo-Csv -NoTypeInformation"],
            capture_output=True, text=True, timeout=15
        )
        lines = result.stdout.strip().splitlines()
        if len(lines) < 2:
            return []

        running_procs = get_running_processes()

        for line in lines[1:]:  # 헤더 제외
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) < 4:
                continue
            name, version, publisher, install_loc = parts[0], parts[1], parts[2], parts[3]
            if not name:
                continue

            name_lower = name.lower()
            is_bloat   = any(kw in name_lower for kw in BLOATWARE_KEYWORDS)
            exe_name   = guess_exe_name(name, install_loc)
            is_running = exe_name.lower() in running_procs
            last_run   = get_last_run_from_prefetch(exe_name)
            size_mb    = get_folder_size_mb(install_loc) if install_loc else 0

            # 아이콘: 설치 경로에서 .png 또는 .ico 찾기
            icon_path = ""
            if install_loc and os.path.isdir(install_loc):
                for root, _, files in os.walk(install_loc):
                    for f in files:
                        if f.lower().endswith((".png", ".ico")):
                            icon_path = os.path.join(root, f)
                            break
                    if icon_path:
                        break

            store_apps.append({
                "이름":         name,
                "_icon_path":   icon_path,
                "버전":         version or "-",
                "제조사":       publisher or "-",
                "설치 날짜":    "-",
                "크기(MB)":     size_mb,
                "실행 중":      "🟢 실행 중" if is_running else "",
                "마지막 실행일": last_run,
                "설치 경로":    install_loc or "-",
                "⚠️ 불필요":    "⚠️ 의심" if is_bloat else "",
                "_uninstall":   f"powershell Remove-AppxPackage {name}",
            })
    except Exception:
        pass
    return store_apps

def get_installed_programs(progress_signal=None):
    programs = []
    seen = set()
    running_procs = get_running_processes()

    total_steps = len(REGISTRY_PATHS)
    for step_idx, (hive, path) in enumerate(REGISTRY_PATHS):
        try:
            key = winreg.OpenKey(hive, path)
        except FileNotFoundError:
            continue

        total_keys = winreg.QueryInfoKey(key)[0] or 1
        for i in range(total_keys):
            if progress_signal and i % 5 == 0:
                base_pct  = int(step_idx / total_steps * 50)
                inner_pct = int(i / total_keys * (50 // total_steps))
                progress_signal.emit(min(base_pct + inner_pct, 49))
            try:
                sub_name = winreg.EnumKey(key, i)
                sub_key  = winreg.OpenKey(key, sub_name)

                def get_val(name):
                    try:
                        return winreg.QueryValueEx(sub_key, name)[0]
                    except FileNotFoundError:
                        return ""

                name             = get_val("DisplayName")
                version          = get_val("DisplayVersion")
                publisher        = get_val("Publisher")
                install_loc      = get_val("InstallLocation")
                uninstall        = get_val("UninstallString")
                install_date_raw = get_val("InstallDate")
                icon_path_raw    = get_val("DisplayIcon")

                if not name or name in seen:
                    continue
                seen.add(name)

                if install_date_raw and len(install_date_raw) == 8:
                    try:
                        install_date = datetime.strptime(install_date_raw, "%Y%m%d").strftime("%Y-%m-%d")
                    except ValueError:
                        install_date = install_date_raw
                else:
                    install_date = "-"

                size_mb = get_folder_size_mb(install_loc) if install_loc else 0

                # 불필요 앱 여부 판단
                name_lower = name.lower()
                is_bloat = any(kw in name_lower for kw in BLOATWARE_KEYWORDS)

                # 실행 여부 & 마지막 실행일
                exe_name     = guess_exe_name(name, install_loc)
                is_running   = exe_name.lower() in running_procs
                last_run     = get_last_run_from_prefetch(exe_name)

                programs.append({
                    "이름":         name,
                    "_icon_path":   icon_path_raw,
                    "버전":         version or "-",
                    "제조사":       publisher or "-",
                    "설치 날짜":    install_date,
                    "크기(MB)":     size_mb,
                    "실행 중":      "🟢 실행 중" if is_running else "",
                    "마지막 실행일": last_run,
                    "설치 경로":    install_loc or "-",
                    "⚠️ 불필요":    "⚠️ 의심" if is_bloat else "",
                    "_uninstall":   uninstall,
                })

            except OSError:
                continue

    if progress_signal: progress_signal.emit(50)
    # Program Files 폴더에서 추가 탐색
    prog_files = [r'C:\Program Files', r'C:\Program Files (x86)']
    seen_names = {p['이름'] for p in programs}
    for base in prog_files:
        if not os.path.isdir(base):
            continue
        try:
            for folder in os.listdir(base):
                folder_path = os.path.join(base, folder)
                if not os.path.isdir(folder_path) or folder in seen_names:
                    continue
                # .exe 있는지 확인
                exe_path = ''
                try:
                    for f in os.listdir(folder_path):
                        if f.lower().endswith('.exe'):
                            exe_path = os.path.join(folder_path, f)
                            break
                except PermissionError:
                    continue
                if not exe_path:
                    continue
                seen_names.add(folder)
                size_mb  = get_folder_size_mb(folder_path)
                exe_name = os.path.basename(exe_path)
                is_running = exe_name.lower() in running_procs
                last_run   = get_last_run_from_prefetch(exe_name)
                programs.append({
                    '이름':         folder,
                    '_icon_path':   exe_path,
                    '버전':         '-',
                    '제조사':       '-',
                    '설치 날짜':    '-',
                    '크기(MB)':     size_mb,
                    '실행 중':      '🟢 실행 중' if is_running else '',
                    '마지막 실행일': last_run,
                    '설치 경로':    folder_path,
                    '⚠️ 불필요':    '',
                    '_uninstall':   '',
                })
        except PermissionError:
            continue

    if progress_signal: progress_signal.emit(80)
    # Microsoft Store 앱 추가
    programs += get_store_apps()
    if progress_signal: progress_signal.emit(100)

    return programs


def get_folder_size_mb(path):
    if not path or not os.path.isdir(path):
        return 0
    total = 0
    try:
        for dirpath, _, filenames in os.walk(path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    pass
    except PermissionError:
        pass
    return round(total / (1024 * 1024), 1)


# ─────────────────────────────────────────────
# 2. 중복 파일 스캔
# ─────────────────────────────────────────────
def file_hash(path, chunk=65536):
    h = hashlib.md5()
    try:
        with open(path, "rb") as f:
            while chunk_data := f.read(chunk):
                h.update(chunk_data)
        return h.hexdigest()
    except (OSError, PermissionError):
        return None

def files_are_identical(path1, path2, chunk=65536):
    """두 파일을 바이트 단위로 직접 비교해서 완전히 같은지 확인"""
    try:
        with open(path1, "rb") as f1, open(path2, "rb") as f2:
            while True:
                b1 = f1.read(chunk)
                b2 = f2.read(chunk)
                if b1 != b2:
                    return False
                if not b1:
                    return True
    except (OSError, PermissionError):
        return True  # 읽기 실패 시 일단 중복으로 간주


def find_duplicate_files(folders, extensions, progress_signal=None):

    # ── 1단계: 파일 목록 수집 ──
    all_files = []
    all_extensions = extensions | EXE_EXTENSIONS
    for folder in folders:
        if not os.path.isdir(folder):
            continue
        for dirpath, _, filenames in os.walk(folder):
            for fname in filenames:
                ext = os.path.splitext(fname)[1].lower()
                if ext in all_extensions:
                    all_files.append(os.path.join(dirpath, fname))

    total_files = len(all_files) or 1

    # ── 2단계: 크기별로 먼저 묶기 ──
    size_map = {}
    for i, fp in enumerate(all_files):
        try:
            size = os.path.getsize(fp)
            size_map.setdefault(size, []).append(fp)
        except OSError:
            continue
        if progress_signal:
            pct = int(i / total_files * 40)  # 0~40%
            progress_signal.emit(pct)

    # ── 3단계: 같은 크기 파일만 해시 비교 ──
    hash_map = {}
    candidates = [files for files in size_map.values() if len(files) > 1]
    total_candidates = sum(len(f) for f in candidates) or 1
    done = 0

    for files in candidates:
        for fp in files:
            h = file_hash(fp)
            if h:
                hash_map.setdefault(h, []).append(fp)
            done += 1
            if progress_signal:
                pct = 40 + int(done / total_candidates * 55)  # 40~95%
                progress_signal.emit(min(pct, 95))

    if progress_signal:
        progress_signal.emit(100)

    # ── 4단계: 중복 목록 생성 ──
    duplicates = []
    for h, paths in hash_map.items():
        if len(paths) > 1:
            size = os.path.getsize(paths[0]) if os.path.exists(paths[0]) else 0

            # 해시 충돌 감지 (내용이 다른데 해시가 같은 경우)
            has_collision = False
            for i in range(len(paths)):
                for j in range(i + 1, len(paths)):
                    if not files_are_identical(paths[i], paths[j]):
                        has_collision = True
                        break

            # 직접 상위 폴더(부모 폴더)가 다른지 확인
            parent_folders = set(os.path.dirname(os.path.normpath(p)) for p in paths)
            diff_folders = len(parent_folders) > 1

            # 파일명이 서로 다른 경우도 경고 (다른 파일인데 해시만 같은 경우)
            file_names = set(os.path.basename(p) for p in paths)
            diff_names = len(file_names) > 1

            for p in paths:
                ext = os.path.splitext(p)[1].lower()
                # 경고 레벨 결정
                # 1순위: 해시 충돌 (내용 다름)
                # 2순위: 다른 폴더 구조에 있는 파일 (각각 필요할 수 있음)
                # 3순위: exe 파일
                # 파일명이 (숫자)만 다른 경우 → 명백한 중복, 경고 없음
                import re as _re
                base_names = set(
                    _re.sub(r"\s*\(\d+\)(\.[^.]+)?$", "", os.path.splitext(os.path.basename(p))[0])
                    for p in paths
                )
                is_numbered_copy = len(base_names) == 1  # 숫자 제거 후 이름이 같으면 복사본

                warn_level = (
                    "collision"    if has_collision                                          else
                    "normal"       if is_numbered_copy and not diff_folders                 else
                    "diff_folders" if diff_folders                                          else
                    "diff_names"   if diff_names and not is_numbered_copy                   else
                    "exe"          if ext in EXE_EXTENSIONS                                 else
                    "normal"
                )
                duplicates.append({
                    "파일명":      os.path.basename(p),
                    "경로":        os.path.normpath(p),
                    "크기(MB)":    round(size / (1024 * 1024), 2),
                    "그룹(해시)":  h[:8],
                    "_is_exe":     ext in EXE_EXTENSIONS,
                    "_collision":  has_collision,
                    "_warn_level": warn_level,
                })
    return duplicates


# ─────────────────────────────────────────────
# 3. 백그라운드 스레드
# ─────────────────────────────────────────────
def is_program_still_installed(name):
    """레지스트리에서 프로그램이 아직 설치돼 있는지 확인"""
    for hive, path in REGISTRY_PATHS:
        try:
            key = winreg.OpenKey(hive, path)
            for i in range(winreg.QueryInfoKey(key)[0]):
                try:
                    sub_name = winreg.EnumKey(key, i)
                    sub_key  = winreg.OpenKey(key, sub_name)
                    reg_name = winreg.QueryValueEx(sub_key, "DisplayName")[0]
                    if reg_name == name:
                        return True
                except (OSError, FileNotFoundError):
                    continue
        except (OSError, FileNotFoundError):
            continue
    return False


class UninstallThread(QThread):
    """언인스톨러를 백그라운드에서 실행하고 완료 시 시그널 전송"""
    finished = pyqtSignal(list, list)  # (완료된 row, 실패한 row)

    def __init__(self, rows_cmds_names):
        super().__init__()
        self.rows_cmds_names = rows_cmds_names  # [(row, cmd, name), ...]

    def run(self):
        done_rows   = []
        failed_rows = []
        for row, cmd, name in self.rows_cmds_names:
            if not cmd:
                failed_rows.append((row, name, "언인스톨러 없음"))
                continue
            try:
                proc = subprocess.Popen(cmd, shell=True)
                proc.wait()
                # 레지스트리에서 실제 삭제 여부 확인
                if is_program_still_installed(name):
                    failed_rows.append((row, name, "삭제되지 않음"))
                else:
                    done_rows.append(row)
            except Exception as e:
                failed_rows.append((row, name, str(e)))
        self.finished.emit(done_rows, failed_rows)


class ProgramScanThread(QThread):
    finished = pyqtSignal(list)
    progress = pyqtSignal(int)   # 0~100

    def run(self):
        self.finished.emit(get_installed_programs(self.progress))

class DupScanThread(QThread):
    finished  = pyqtSignal(list)
    progress  = pyqtSignal(int)  # 0~100
    def __init__(self, folders, extensions):
        super().__init__()
        self.folders     = folders
        self.extensions  = extensions
    def run(self):
        result = find_duplicate_files(self.folders, self.extensions, self.progress)
        self.finished.emit(result)


# ─────────────────────────────────────────────
# 4. 탭 1 – 설치 프로그램 관리
# ─────────────────────────────────────────────
PROG_COLUMNS = ["✔", "이름", "버전", "제조사", "설치 날짜", "크기(MB)", "실행 중", "마지막 실행일", "설치 경로", "⚠️ 불필요"]

class ProgramTab(QWidget):
    def __init__(self):
        super().__init__()
        self.all_data = []
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # 상단 바
        top = QHBoxLayout()
        self.search_box = QLineEdit(placeholderText="프로그램 이름 검색...")
        self.search_box.textChanged.connect(self._apply_filter)

        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["이름 순", "크기 큰 순", "설치 날짜 최신 순", "⚠️ 불필요 앱 먼저"])
        self.sort_combo.currentIndexChanged.connect(self._apply_filter)

        self.scan_btn = QPushButton("🔄 다시 스캔")
        self.scan_btn.clicked.connect(self.start_scan)

        self.uninstall_btn = QPushButton("🗑️ 선택 삭제")
        self.uninstall_btn.clicked.connect(self._uninstall_selected)
        self.uninstall_btn.setEnabled(False)

        top.addWidget(QLabel("🔍"))
        top.addWidget(self.search_box, stretch=3)
        top.addWidget(self.sort_combo, stretch=1)
        top.addWidget(self.scan_btn)
        top.addWidget(self.uninstall_btn)
        layout.addLayout(top)

        self.status_label = QLabel("스캔 중...")
        layout.addWidget(self.status_label)

        self.prog_bar = QProgressBar()
        self.prog_bar.setRange(0, 100)
        self.prog_bar.setValue(0)
        self.prog_bar.setFixedHeight(16)
        self.prog_bar.setFormat("%p%")
        self.prog_bar.hide()
        layout.addWidget(self.prog_bar)

        # 테이블
        self.table = QTableWidget(0, len(PROG_COLUMNS))
        self.table.setHorizontalHeaderLabels(PROG_COLUMNS)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        col_widths = {
            "✔":          30,
            "이름":       220,
            "버전":        80,
            "제조사":     130,
            "설치 날짜":   100,
            "크기(MB)":    80,
            "실행 중":     70,
            "마지막 실행일": 110,
            "설치 경로":  280,
            "⚠️ 불필요":   80,
        }
        for col, key in enumerate(PROG_COLUMNS):
            self.table.setColumnWidth(col, col_widths.get(key, 100))
        header.setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemChanged.connect(self._on_check_changed)
        self.table.itemDoubleClicked.connect(self._open_install_location)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu_prog)
        layout.addWidget(self.table)

        self.start_scan()

    def _on_check_changed(self, item):
        if item.column() == 0:
            checked = sum(
                1 for r in range(self.table.rowCount())
                if self.table.item(r, 0) and
                   self.table.item(r, 0).checkState() == Qt.CheckState.Checked
            )
            self.uninstall_btn.setEnabled(checked > 0)
            self.uninstall_btn.setText(f"🗑️ 선택 삭제 ({checked})" if checked else "🗑️ 선택 삭제")

    def _show_context_menu_prog(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        menu = QMenu(self)
        action_run    = menu.addAction("▶️ 프로그램 실행")
        action_uninst = menu.addAction("🗑️ 프로그램 삭제")
        action = menu.exec(self.table.viewport().mapToGlobal(pos))
        if action == action_run:
            path = self.table.item(row, 8).text()  # 설치 경로
            if path and path != "-" and os.path.isdir(path):
                # 하위 폴더까지 재귀 탐색해서 .exe 찾기
                exe_found = None
                for dirpath, _, files in os.walk(path):
                    for f in files:
                        if f.lower().endswith(".exe"):
                            exe_found = os.path.join(dirpath, f)
                            break
                    if exe_found:
                        break
                if exe_found:
                    try:
                        subprocess.Popen(exe_found)
                    except Exception as e:
                        QMessageBox.warning(self, "실행 실패", f"실행할 수 없어요:\n{e}")
                else:
                    QMessageBox.information(self, "실행 불가", "실행 파일을 찾을 수 없어요.")
            else:
                QMessageBox.information(self, "실행 불가", "설치 경로 정보가 없어요.")
        elif action == action_uninst:
            self.table.item(row, 0).setCheckState(Qt.CheckState.Checked)
            self._uninstall_selected()

    def _open_install_location(self, item):
        row  = item.row()
        path = self.table.item(row, 8).text()  # "설치 경로" 컬럼

        # 경로가 없는 경우 Program Files에서 이름으로 추측
        if not path or path == "-":
            name = self.table.item(row, 0).text()
            for base in [r"C:\Program Files", r"C:\Program Files (x86)"]:
                guess = os.path.join(base, name.split()[0])
                if os.path.isdir(guess):
                    path = guess
                    break

        if path and path != "-" and os.path.isdir(path):
            subprocess.Popen(f'explorer "{path}"')
        else:
            QMessageBox.information(self, "경로 없음", "설치 경로를 찾을 수 없어요.\n레지스트리에 경로 정보가 없는 프로그램이에요.")

    def start_scan(self):
        self.scan_btn.setEnabled(False)
        self.status_label.setText("스캔 중... 잠시만 기다려주세요.")
        self.prog_bar.show()
        self.table.setRowCount(0)
        self.thread = ProgramScanThread()
        self.thread.progress.connect(self.prog_bar.setValue)
        self.thread.finished.connect(self._on_done)
        self.thread.start()

    def _on_done(self, data):
        self.all_data = data
        self._apply_filter()
        self.scan_btn.setEnabled(True)
        self.prog_bar.hide()
        bloat_count = sum(1 for p in data if p["⚠️ 불필요"])
        self.status_label.setText(
            f"총 {len(data)}개 프로그램 발견  |  ⚠️ 불필요 의심 {bloat_count}개"
        )

    def _apply_filter(self):
        keyword  = self.search_box.text().lower()
        sort_idx = self.sort_combo.currentIndex()
        filtered = [p for p in self.all_data if keyword in p["이름"].lower()]

        if sort_idx == 0:
            filtered.sort(key=lambda x: x["이름"].lower())
        elif sort_idx == 1:
            filtered.sort(key=lambda x: x["크기(MB)"], reverse=True)
        elif sort_idx == 2:
            filtered.sort(key=lambda x: x["설치 날짜"], reverse=True)
        elif sort_idx == 3:
            filtered.sort(key=lambda x: x["⚠️ 불필요"], reverse=True)

        self.table.blockSignals(True)
        self.table.setRowCount(len(filtered))
        for row, prog in enumerate(filtered):
            for col, key in enumerate(PROG_COLUMNS):
                if key == "✔":
                    chk = QTableWidgetItem()
                    chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                    chk.setCheckState(Qt.CheckState.Unchecked)
                    chk.setData(Qt.ItemDataRole.UserRole, prog.get("_uninstall", ""))
                    self.table.setItem(row, col, chk)
                else:
                    val  = prog.get(key, "-")
                    item = QTableWidgetItem(str(val))
                    if prog["⚠️ 불필요"]:
                        item.setBackground(QColor(255, 235, 235))
                    # 이름 컬럼에 아이콘 표시
                    if key == "이름":
                        icon = get_program_icon(
                            prog.get("_icon_path", ""),
                            prog.get("설치 경로", "")
                        )
                        item.setIcon(icon)
                    self.table.setItem(row, col, item)
        self.table.blockSignals(False)

    def _uninstall_selected(self):
        selected_rows = [
            r for r in range(self.table.rowCount())
            if self.table.item(r, 0) and
               self.table.item(r, 0).checkState() == Qt.CheckState.Checked
        ]
        if not selected_rows:
            return
        names = [self.table.item(r, 1).text() for r in selected_rows]
        reply = QMessageBox.question(
            self, "삭제 확인",
            "다음 프로그램을 삭제할까요?\n\n" + "\n".join(f"• {n}" for n in names),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        rows_cmds_names = []
        no_uninstaller  = []

        for row in selected_rows:
            cmd  = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            name = self.table.item(row, 1).text()
            if cmd and cmd.strip():  # 빈 문자열도 언인스톨러 없음으로 처리
                rows_cmds_names.append((row, cmd, name))
            else:
                no_uninstaller.append(name)

        # 언인스톨러 없는 프로그램 경고
        if no_uninstaller:
            QMessageBox.warning(
                self, "언인스톨러 없음",
                "다음 프로그램은 언인스톨러 정보가 없어서 자동 삭제가 불가능해요.\n"
                "직접 설치 폴더를 찾아서 제거해주세요.\n\n"
                + "\n".join(f"• {n}" for n in no_uninstaller)
            )
            if not rows_cmds_names:
                return

        # 삭제 중 UI 비활성화
        self.uninstall_btn.setEnabled(False)
        self.scan_btn.setEnabled(False)
        self.uninstall_btn.setText("⏳ 삭제 중...")
        self.status_label.setText("언인스톨러 실행 중... 완료될 때까지 기다려주세요.")

        self.uninstall_thread = UninstallThread(rows_cmds_names)
        self.uninstall_thread.finished.connect(self._on_uninstall_done)
        self.uninstall_thread.start()

    def _on_uninstall_done(self, done_rows, failed_rows):
        # 성공한 행만 제거 (역순)
        for row in sorted(done_rows, reverse=True):
            self.table.removeRow(row)

        self.uninstall_btn.setText("🗑️ 선택 삭제")
        self.uninstall_btn.setEnabled(False)
        self.scan_btn.setEnabled(True)

        if failed_rows:
            fail_msg = ""
            for _, name, reason in failed_rows:
                if reason == "삭제되지 않음":
                    fail_msg += f"• {name} — 언인스톨러가 실행됐지만 삭제되지 않았어요.\n  런처(예: HoYoPlay, Steam)를 통해 직접 삭제해주세요.\n"
                elif reason == "언인스톨러 없음":
                    fail_msg += f"• {name} — 언인스톨러 정보가 없어요.\n  설치 폴더를 직접 찾아서 제거해주세요.\n"
                else:
                    fail_msg += f"• {name} — {reason}\n"
            QMessageBox.warning(
                self, "일부 삭제 실패",
                f"다음 프로그램은 삭제되지 않아 목록에 유지돼요:\n\n{fail_msg}"
            )
            self.status_label.setText(f"✅ {len(done_rows)}개 삭제 완료  |  ⚠️ {len(failed_rows)}개 실패")
        else:
            self.status_label.setText(f"✅ {len(done_rows)}개 프로그램 삭제 완료")


# ─────────────────────────────────────────────
# 5. 탭 2 – 중복 파일 탐지
# ─────────────────────────────────────────────
DUP_COLUMNS = ["✔", "파일명", "경로", "크기(MB)", "그룹(해시)"]

class DuplicateTab(QWidget):
    def __init__(self):
        super().__init__()
        self.scan_folders = list(SCAN_FOLDERS)
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # 폴더 선택
        folder_row = QHBoxLayout()
        self.folder_label = QLabel(f"스캔 폴더: 문서, 다운로드, 바탕화면, 사진")
        self.folder_label.setWordWrap(True)
        add_btn = QPushButton("📁 폴더 추가")
        add_btn.clicked.connect(self._add_folder)
        folder_row.addWidget(self.folder_label, stretch=4)
        folder_row.addWidget(add_btn)
        layout.addLayout(folder_row)

        # 스캔 버튼
        self._dup_selected = False
        self._safe_mode    = True   # 기본: 안전 모드
        self._warn_enabled = True   # 기본: 경고 알림 켜짐

        btn_row = QHBoxLayout()
        self.scan_btn = QPushButton("🔍 중복 파일 스캔 시작")
        self.scan_btn.clicked.connect(self._start_scan)
        self.dup_btn = QPushButton("🔁 중복 선택")
        self.dup_btn.clicked.connect(self._select_duplicates)
        self.dup_btn.setEnabled(False)
        self.delete_btn = QPushButton("🗑️ 선택 파일 삭제")
        self.delete_btn.clicked.connect(self._delete_selected)
        self.delete_btn.setEnabled(False)
        self.setting_btn = QPushButton("⚙️ 설정")
        self.setting_btn.clicked.connect(self._show_settings)
        btn_row.addWidget(self.scan_btn)
        btn_row.addWidget(self.dup_btn)
        btn_row.addWidget(self.delete_btn)
        btn_row.addWidget(self.setting_btn)
        layout.addLayout(btn_row)

        self.status_label = QLabel("스캔 버튼을 눌러 중복 파일을 찾아보세요.")
        layout.addWidget(self.status_label)

        # 범례
        legend = QLabel(
            "  ✅ 일반 중복 (안전하게 삭제 가능)　"
            "⚠️ 다른 폴더 / 실행파일 (각각 필요할 수 있음)　"
            "🚨 파일명 다름 / 내용 불일치 (삭제 주의)　"
            "💡 마우스 오버 시 상세 설명"
        )
        legend.setStyleSheet(
            "background: #f8f8f8; border: 1px solid #ddd;"
            "border-radius: 4px; padding: 4px 8px; color: #555; font-size: 11px;"
        )
        legend.setWordWrap(True)
        layout.addWidget(legend)

        self.dup_bar = QProgressBar()
        self.dup_bar.setRange(0, 100)
        self.dup_bar.setValue(0)
        self.dup_bar.setFixedHeight(16)
        self.dup_bar.setFormat("%p%")
        layout.addWidget(self.dup_bar)

        # 테이블
        self.table = QTableWidget(0, len(DUP_COLUMNS))
        self.table.setHorizontalHeaderLabels(DUP_COLUMNS)
        dup_header = self.table.horizontalHeader()
        dup_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        dup_col_widths = {"✔": 30, "파일명": 200, "경로": 300, "크기(MB)": 80, "그룹(해시)": 90}
        for col, key in enumerate(DUP_COLUMNS):
            self.table.setColumnWidth(col, dup_col_widths.get(key, 100))
        dup_header.setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemChanged.connect(self._on_check_changed)
        self.table.itemDoubleClicked.connect(self._open_file_location)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu_dup)
        layout.addWidget(self.table)

    def _show_context_menu_dup(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        menu = QMenu(self)
        action_open   = menu.addAction("📂 파일 위치 열기")
        action_delete = menu.addAction("🗑️ 파일 삭제")
        action = menu.exec(self.table.viewport().mapToGlobal(pos))
        if action == action_open:
            self._open_file_location(self.table.item(row, 0))
        elif action == action_delete:
            self.table.item(row, 0).setCheckState(Qt.CheckState.Checked)
            self._delete_selected()

    def _on_check_changed(self, item):
        if item.column() == 0:
            checked = sum(
                1 for r in range(self.table.rowCount())
                if self.table.item(r, 0) and
                   self.table.item(r, 0).checkState() == Qt.CheckState.Checked
            )
            self.delete_btn.setEnabled(checked > 0)
            self.delete_btn.setText(f"🗑️ 선택 파일 삭제 ({checked})" if checked else "🗑️ 선택 파일 삭제")
            # 수동으로 체크 변경 시 토글 상태 초기화
            self._dup_selected = False
            self.dup_btn.setText("🔁 중복 선택")

    def _select_duplicates(self):
        import re
        self.table.blockSignals(True)

        if self._dup_selected:
            # 이미 선택된 상태 → 전체 해제
            for r in range(self.table.rowCount()):
                if self.table.item(r, 0):
                    self.table.item(r, 0).setCheckState(Qt.CheckState.Unchecked)
            self._dup_selected = False
            self.dup_btn.setText("🔁 중복 선택")
            self.delete_btn.setEnabled(False)
            self.delete_btn.setText("🗑️ 선택 파일 삭제")
        else:
            # 그룹별 행 수집
            group_rows = {}
            for r in range(self.table.rowCount()):
                g = self.table.item(r, 4).text()
                group_rows.setdefault(g, []).append(r)

            def is_original(r):
                fname = self.table.item(r, 1).text()
                base  = os.path.splitext(fname)[0].lstrip("⚠️ ")
                return not re.search(r"\s*\(\d+\)$", base)

            for rows in group_rows.values():
                originals = [r for r in rows if is_original(r)]
                origin    = originals[0] if originals else rows[0]
                for r in rows:
                    fname = self.table.item(r, 1).text()
                    warn  = fname.startswith("🚨") or fname.startswith("⚠️")
                    # 안전 모드: 위험 파일은 체크 안 함
                    if self._safe_mode and warn:
                        state = Qt.CheckState.Unchecked
                    else:
                        state = Qt.CheckState.Unchecked if r == origin else Qt.CheckState.Checked
                    self.table.item(r, 0).setCheckState(state)

            self._dup_selected = True
            checked = sum(
                1 for r in range(self.table.rowCount())
                if self.table.item(r, 0) and
                   self.table.item(r, 0).checkState() == Qt.CheckState.Checked
            )
            self.dup_btn.setText("🔁 중복 선택 취소")
            self.delete_btn.setEnabled(checked > 0)
            self.delete_btn.setText(f"🗑️ 선택 파일 삭제 ({checked})" if checked else "🗑️ 선택 파일 삭제")

        self.table.blockSignals(False)

    def _show_settings(self):
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QCheckBox, QDialogButtonBox
        dialog = QDialog(self)
        dialog.setWindowTitle("⚙️ 중복 선택 설정")
        dialog.setFixedWidth(380)
        layout = QVBoxLayout(dialog)

        desc = QLabel(
            "중복 선택 버튼 동작 방식을 설정해요.\n"
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        safe_chk = QCheckBox("🛡️ 안전 모드 (기본 권장)")
        safe_chk.setChecked(self._safe_mode)
        safe_chk.setToolTip(
            "위험 파일(다른 폴더, 파일명 다름, 실행파일 등)은\n"
            "중복 선택 시 자동으로 제외해요.\n"
            "직접 확인 후 수동으로 체크할 수 있어요."
        )
        layout.addWidget(safe_chk)

        safe_desc = QLabel(
            "  • 같은 폴더 내 중복만 자동 체크\n"
            "  • ⚠️ 🚨 표시 파일은 자동 제외"
        )
        safe_desc.setStyleSheet("color: gray; margin-left: 20px;")
        layout.addWidget(safe_desc)

        full_chk = QCheckBox("⚡ 전체 모드")
        full_chk.setChecked(not self._safe_mode)
        full_chk.setToolTip("원본 1개만 남기고 모두 체크해요.\n경고 파일도 포함돼요.")
        layout.addWidget(full_chk)

        full_desc = QLabel(
            "  • 원본 1개 빼고 전부 자동 체크\n"
            "  • ⚠️ 🚨 파일도 포함 (삭제 시 경고창 표시)"
        )
        full_desc.setStyleSheet("color: gray; margin-left: 20px;")
        layout.addWidget(full_desc)

        # 라디오처럼 동작
        def on_safe(checked):
            if checked:
                full_chk.setChecked(False)
        def on_full(checked):
            if checked:
                safe_chk.setChecked(False)
        safe_chk.toggled.connect(on_safe)
        full_chk.toggled.connect(on_full)

        # 구분선
        line = QLabel("─" * 40)
        line.setStyleSheet("color: lightgray;")
        layout.addWidget(line)

        # 경고 알림 설정
        warn_title = QLabel("🔔 경고 알림 설정")
        warn_title.setStyleSheet("font-weight: bold;")
        layout.addWidget(warn_title)

        warn_chk = QCheckBox("삭제 시 경고 알림 표시")
        warn_chk.setChecked(self._warn_enabled)
        warn_chk.setToolTip("⚠️ 🚨 파일 삭제 시 경고창을 표시해요.")
        layout.addWidget(warn_chk)

        warn_desc = QLabel(
            "  • 켜짐: 위험 파일 삭제 시 경고창 표시\n"
            "  • 꺼짐: 경고 없이 바로 삭제 진행"
        )
        warn_desc.setStyleSheet("color: gray; margin-left: 20px;")
        layout.addWidget(warn_desc)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dialog.accept)
        btns.rejected.connect(dialog.reject)
        layout.addWidget(btns)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._safe_mode    = safe_chk.isChecked()
            self._warn_enabled = warn_chk.isChecked()
            mode = "🛡️ 안전 모드" if self._safe_mode else "⚡ 전체 모드"
            self.setting_btn.setText(f"⚙️ 설정 ({mode})")

    def _open_file_location(self, item):
        row  = item.row()
        path = self.table.item(row, 2).text()  # "경로" 컬럼 (✔ 추가로 인덱스 +1)
        path = resolve_shortcut(path)  # 바로가기면 원본 경로로
        if path and os.path.exists(path):
            path = os.path.normpath(path)
            subprocess.run(f'explorer /select,"{path}"', shell=True)
        else:
            QMessageBox.information(self, "파일 없음", "파일을 찾을 수 없어요.\n이미 삭제됐을 수 있어요.")

    def _add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "폴더 선택")
        if folder and folder not in self.scan_folders:
            self.scan_folders.append(folder)
            self.folder_label.setText(f"스캔 폴더: {', '.join(os.path.basename(f) for f in self.scan_folders)}")

    def _start_scan(self):
        self.scan_btn.setEnabled(False)
        self.table.setRowCount(0)
        self.status_label.setText("스캔 중... 파일이 많으면 시간이 걸릴 수 있어요.")
        self.thread = DupScanThread(self.scan_folders, SCAN_EXTENSIONS)
        self.thread.progress.connect(self.dup_bar.setValue)
        self.thread.finished.connect(self._on_done)
        self.thread.start()

    def _on_done(self, data):
        self.scan_btn.setEnabled(True)
        self.dup_bar.setValue(100)
        self._dup_selected = False
        self.dup_btn.setText("🔁 중복 선택")
        if not data:
            self.status_label.setText("✅ 중복 파일이 없어요!")
            self.dup_btn.setEnabled(False)
            return
        self.dup_btn.setEnabled(True)

        wasted = sum(r["크기(MB)"] for r in data) / 2  # 절반이 중복
        self.status_label.setText(
            f"중복 파일 {len(data)}개 발견  |  절약 가능 용량: 약 {wasted:.1f} MB"
        )
        self.table.blockSignals(True)
        self.table.setRowCount(len(data))

        group_colors = {}
        color_list = [QColor(255, 243, 205), QColor(205, 230, 255), QColor(220, 255, 220)]
        for row, item in enumerate(data):
            g = item["그룹(해시)"]
            if g not in group_colors:
                group_colors[g] = color_list[len(group_colors) % len(color_list)]
            color = group_colors[g]
            is_exe      = item.get("_is_exe", False)
            is_collision = item.get("_collision", False)
            warn_level   = item.get("_warn_level", "normal")
            for col, key in enumerate(DUP_COLUMNS):
                if key == "✔":
                    chk = QTableWidgetItem()
                    chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                    chk.setCheckState(Qt.CheckState.Unchecked)
                    chk.setBackground(color)
                    self.table.setItem(row, col, chk)
                else:
                    val = str(item.get(key, "-"))
                    tooltip = ""
                    if key == "파일명":
                        if warn_level == "collision":
                            val     = "🚨 " + val
                            tooltip = "해시값은 같지만 실제 내용이 달라요! 삭제 전 반드시 확인하세요."
                        elif warn_level == "diff_names":
                            val     = "🚨 " + val
                            tooltip = "파일명이 다른데 해시값이 같아요! 서로 다른 파일일 수 있어요. 삭제 전 반드시 확인하세요."
                        elif warn_level == "diff_folders":
                            val     = "⚠️ " + val
                            tooltip = "다른 폴더 구조에 있는 파일이에요. 각각 필요한 파일일 수 있어요!"
                        elif warn_level == "exe":
                            val     = "⚠️ " + val
                            tooltip = "실행파일이에요. 삭제 전 확인하세요."
                    cell = QTableWidgetItem(val)
                    if warn_level in ("collision", "diff_names"):
                        cell.setForeground(QColor(180, 0, 0))    # 빨간색
                    elif warn_level in ("diff_folders", "exe"):
                        cell.setForeground(QColor(180, 80, 0))   # 주황색
                    if tooltip:
                        cell.setToolTip(tooltip)
                    cell.setBackground(color)
                    # 파일명 컬럼에 아이콘 표시
                    if key == "파일명":
                        path = item.get("경로", "")
                        if path.lower().endswith(".exe"):
                            icon = extract_icon_from_exe(path)
                        elif path.lower().endswith((".ico", ".png")):
                            icon = QIcon(path)
                        else:
                            icon = None
                        if icon and not icon.isNull():
                            cell.setIcon(icon)
                    self.table.setItem(row, col, cell)
        self.table.blockSignals(False)

    def _delete_selected(self):
        selected_rows = [
            r for r in range(self.table.rowCount())
            if self.table.item(r, 0) and
               self.table.item(r, 0).checkState() == Qt.CheckState.Checked
        ]
        paths = [self.table.item(r, 2).text() for r in selected_rows]

        # 🚨 해시 충돌 / 파일명 다른 경고
        collision_rows = [
            r for r in selected_rows
            if self.table.item(r, 1) and self.table.item(r, 1).text().startswith("🚨")
        ]
        if collision_rows and self._warn_enabled:
            col_paths = [self.table.item(r, 2).text() for r in collision_rows]
            col_names = [self.table.item(r, 1).text() for r in collision_rows]
            has_diff_name = any("파일명이 다른" in (self.table.item(r, 1).toolTip() or "") for r in collision_rows)
            msg = (
                f"선택한 파일 중 {len(collision_rows)}개에서 문제가 감지됐어요!\n\n"
                + "\n".join(f"• {os.path.basename(p)}" for p in col_paths[:5])
                + ("\n..." if len(col_paths) > 5 else "")
                + "\n\n파일명이 다른데 해시가 같거나, 내용이 다른 파일이 포함돼 있어요.\n"
                "삭제하면 복구가 어려울 수 있어요. 계속 진행할까요?"
            )
            col_reply = QMessageBox.warning(
                self, "🚨 위험 경고", msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel
            )
            if col_reply == QMessageBox.StandardButton.Cancel:
                return

        # ⚠️ 다른 폴더 구조 파일 경고
        diff_rows = [
            r for r in selected_rows
            if self.table.item(r, 1) and self.table.item(r, 1).text().startswith("⚠️")
            and not os.path.splitext(self.table.item(r, 2).text())[1].lower() in EXE_EXTENSIONS
        ]
        if diff_rows and self._warn_enabled:
            diff_paths = [self.table.item(r, 2).text() for r in diff_rows]
            diff_reply = QMessageBox.warning(
                self, "⚠️ 다른 폴더 경고",
                f"선택한 파일 중 {len(diff_rows)}개가 서로 다른 폴더 구조에 있어요.\n"
                "각각 다른 프로그램/프로젝트에서 필요한 파일일 수 있어요!\n\n"
                + "\n".join(f"• {os.path.basename(p)}" for p in diff_paths[:5])
                + "\n\n파일 위치를 먼저 확인할까요?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel
            )
            if diff_reply == QMessageBox.StandardButton.Cancel:
                return
            if diff_reply == QMessageBox.StandardButton.Yes:
                for p in diff_paths[:3]:  # 최대 3개만 열기
                    if os.path.exists(p):
                        subprocess.run(f'explorer /select,"{p}"', shell=True)
                return

        # ⚠️ exe 파일 포함 여부 확인
        exe_paths = [p for p in paths if os.path.splitext(p)[1].lower() in EXE_EXTENSIONS]
        if exe_paths and self._warn_enabled:
            warn_msg = (
                f"⚠️ 선택한 파일 중 {len(exe_paths)}개가 실행파일(.exe/.msi)이에요!\n\n"
                + "\n".join(f"• {os.path.basename(p)}" for p in exe_paths[:5])
                + ("\n..." if len(exe_paths) > 5 else "")
                + "\n\n다른 폴더에 있는 같은 이름의 파일일 수 있어요.\n파일 위치를 먼저 확인하시겠어요?"
            )
            warn_reply = QMessageBox.warning(
                self, "실행파일 삭제 경고", warn_msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel
            )
            if warn_reply == QMessageBox.StandardButton.Cancel:
                return
            if warn_reply == QMessageBox.StandardButton.Yes:
                for p in exe_paths:
                    if os.path.exists(p):
                        subprocess.run(f'explorer /select,"{p}"', shell=True)
                return  # 위치 확인 후 다시 선택하도록

        reply = QMessageBox.question(
            self, "파일 삭제 확인",
            f"{len(paths)}개 파일을 삭제할까요?\n\n" + "\n".join(f"• {p}" for p in paths[:5])
            + ("\n..." if len(paths) > 5 else ""),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        errors = []
        deleted_rows = []
        for i, (row, path) in enumerate(zip(selected_rows, paths)):
            try:
                os.remove(path)
                deleted_rows.append(row)
            except Exception as e:
                errors.append(str(e))

        # 삭제된 행만 위에서부터 제거 (역순으로 제거해야 인덱스 안 밀림)
        for row in sorted(deleted_rows, reverse=True):
            self.table.removeRow(row)

        # 상태 업데이트
        remaining = self.table.rowCount()
        self.status_label.setText(f"총 {remaining}개 파일 남음")
        self.delete_btn.setText("🗑️ 선택 파일 삭제")
        self.delete_btn.setEnabled(False)

        if errors:
            QMessageBox.warning(self, "일부 실패", "\n".join(errors))
        else:
            QMessageBox.information(self, "완료", f"{len(deleted_rows)}개 파일을 삭제했어요.")




# ─────────────────────────────────────────────
# 6. 메인 윈도우
# ─────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PC 설치파일 관리자")
        self.resize(1150, 680)

        tabs = QTabWidget()
        tabs.addTab(ProgramTab(),   "📦 설치 프로그램")
        tabs.addTab(DuplicateTab(), "🔁 중복 파일 탐지")
        self.setCentralWidget(tabs)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
