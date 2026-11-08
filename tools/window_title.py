"""Keep the game window's title set to a given text while the game runs (the title is in the recompiled exe, so it
is renamed from outside with SetWindowTextW). Started detached by the GUI launcher; ends when the game ends.

usage: window_title.py <game pid> <exe path> <title>
"""
import ctypes
import ctypes.wintypes as wt
import sys
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x100000


def exe_of(pid):
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wt.DWORD(len(buf))
        return buf.value if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)) else None
    finally:
        kernel32.CloseHandle(h)


def game_windows(pid, exe):
    """Visible top-level windows of the game: of its pid, or of any process running its exe."""
    found = []

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, 4):   # GW_OWNER: skip owned popups
            p = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
            if p.value == pid or (exe and (exe_of(p.value) or "").lower() == exe.lower()):
                found.append(hwnd)
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return found


def alive(pid, exe):
    h = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if h:
        try:
            return kernel32.WaitForSingleObject(h, 0) == 0x102   # WAIT_TIMEOUT: still running
        finally:
            kernel32.CloseHandle(h)
    return bool(game_windows(-1, exe))


def main():
    pid, exe, title = int(sys.argv[1]), sys.argv[2], sys.argv[3]
    buf = ctypes.create_unicode_buffer(512)
    seen_window = False
    start = time.time()
    while True:
        wins = game_windows(pid, exe)
        for hwnd in wins:
            seen_window = True
            user32.GetWindowTextW(hwnd, buf, len(buf))
            if buf.value != title:
                user32.SetWindowTextW(hwnd, title)
        if not wins and (seen_window or time.time() - start > 300) and not alive(pid, exe):
            return
        if not wins and time.time() - start > 600:
            return
        time.sleep(1)


if __name__ == "__main__":
    main()
