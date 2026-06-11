"""
tray_win.py — Windows system tray icon (ctypes, no extra deps)

The icon lives on its OWN message-only window in a dedicated thread.
Do not subclass the Tk window: a ctypes wndproc on Tk's HWND runs
re-entrantly inside Tk's event loop while the GIL is released, and calling
tkinter from there hard-crashes the interpreter (PyEval_RestoreThread fatal
error). The on_show/on_quit callbacks fire on the tray thread — they must be
thread-safe (e.g. set a threading.Event polled by the GUI), never call Tk.
"""

import ctypes
import threading
from ctypes import wintypes

# Python 3.14+ removed LONG_PTR from ctypes.wintypes
if hasattr(wintypes, "LONG_PTR"):
    LONG_PTR = wintypes.LONG_PTR
else:
    LONG_PTR = ctypes.c_ssize_t

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32
kernel32 = ctypes.windll.kernel32

NIM_ADD = 0x00000000
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004

WM_NULL = 0x0000
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_APP_TRAY = 0x8001  # WM_APP + 1: tray callback message

IDI_APPLICATION = 32512
TPM_RIGHTALIGN = 0x0008
TPM_BOTTOMALIGN = 0x0020
TPM_RETURNCMD = 0x0100
MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
HWND_MESSAGE = -3

ID_SHOW = 1001
ID_EXIT = 1002

_CLASS_NAME = "StreamRecorderTrayWnd"


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]


WNDPROC = ctypes.WINFUNCTYPE(
    LONG_PTR,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


user32.DefWindowProcW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
]
user32.DefWindowProcW.restype = LONG_PTR
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
user32.CreateWindowExW.restype = wintypes.HWND


class WinTray:
    """System tray icon on a dedicated thread.

    on_show / on_quit are invoked on the TRAY thread — keep them
    thread-safe (set a flag/Event; never call tkinter directly).
    """

    def __init__(self, tooltip: str, on_show, on_quit):
        self.tooltip = tooltip
        self.on_show = on_show
        self.on_quit = on_quit
        self.uID = 1
        self.hwnd = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._failed = threading.Event()
        self._wndproc_cb = None  # keep callback alive

    # ── public API ────────────────────────────────────────────────────────────

    def add(self):
        if self._thread and self._thread.is_alive():
            return
        self._ready.clear()
        self._failed.clear()
        self._thread = threading.Thread(
            target=self._thread_main, daemon=True, name="win-tray"
        )
        self._thread.start()
        self._ready.wait(timeout=5)
        if self._failed.is_set() or not self._ready.is_set():
            raise OSError("tray icon could not be created")

    def remove(self):
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        self.hwnd = None

    @staticmethod
    def _load_app_icon():
        import os
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "icons", "devil.ico")
        if not os.path.isfile(path):
            return None
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x0010
        LR_DEFAULTSIZE = 0x0040
        user32.LoadImageW.restype = wintypes.HANDLE
        return user32.LoadImageW(None, path, IMAGE_ICON, 0, 0,
                                 LR_LOADFROMFILE | LR_DEFAULTSIZE)

    # ── tray thread ───────────────────────────────────────────────────────────

    def _thread_main(self):
        try:
            hinst = kernel32.GetModuleHandleW(None)

            @WNDPROC
            def wndproc(hWnd, uMsg, wParam, lParam):
                if uMsg == WM_APP_TRAY:
                    if lParam in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                        self.on_show()
                    elif lParam == WM_RBUTTONUP:
                        self._show_menu(hWnd)
                    return 0
                if uMsg == WM_DESTROY:
                    user32.PostQuitMessage(0)
                    return 0
                return user32.DefWindowProcW(hWnd, uMsg, wParam, lParam)

            self._wndproc_cb = wndproc

            wc = WNDCLASSW()
            wc.lpfnWndProc = wndproc
            wc.hInstance = hinst
            wc.lpszClassName = _CLASS_NAME
            # Re-register fresh so the class always points at THIS callback
            user32.UnregisterClassW(_CLASS_NAME, hinst)
            if not user32.RegisterClassW(ctypes.byref(wc)):
                self._failed.set()
                self._ready.set()
                return

            self.hwnd = user32.CreateWindowExW(
                0, _CLASS_NAME, "tray", 0, 0, 0, 0, 0,
                HWND_MESSAGE, None, hinst, None,
            )
            if not self.hwnd:
                self._failed.set()
                self._ready.set()
                return

            nid = self._nid()
            nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            nid.uCallbackMessage = WM_APP_TRAY
            nid.hIcon = self._load_app_icon() or user32.LoadIconW(0, IDI_APPLICATION)
            nid.szTip = self.tooltip[:127]
            if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
                user32.DestroyWindow(self.hwnd)
                self._failed.set()
                self._ready.set()
                return

            self._ready.set()

            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))

            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid()))
        except Exception:
            self._failed.set()
            self._ready.set()

    def _nid(self) -> NOTIFYICONDATAW:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = self.uID
        return nid

    def _show_menu(self, hwnd):
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, MF_STRING, ID_SHOW, "Show Scr33nX")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, ID_EXIT, "Exit")
        pos = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pos))
        user32.SetForegroundWindow(hwnd)
        cmd = user32.TrackPopupMenu(
            menu,
            TPM_RIGHTALIGN | TPM_BOTTOMALIGN | TPM_RETURNCMD,
            pos.x,
            pos.y,
            0,
            hwnd,
            None,
        )
        # Required after TrackPopupMenu with TPM_RETURNCMD so the menu closes
        # correctly when the user clicks elsewhere (MSDN tray-menu quirk).
        user32.PostMessageW(hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(menu)
        if cmd == ID_SHOW:
            self.on_show()
        elif cmd == ID_EXIT:
            self.on_quit()

    def pump(self):
        pass
