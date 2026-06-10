"""
tray_win.py — Windows system tray icon (ctypes, no extra deps)
"""

import ctypes
from ctypes import wintypes

# Python 3.14+ removed LONG_PTR from ctypes.wintypes
if hasattr(wintypes, "LONG_PTR"):
    LONG_PTR = wintypes.LONG_PTR
else:
    LONG_PTR = ctypes.c_ssize_t

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32

NIM_ADD = 0x00000000
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004

WM_NULL = 0x0000
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205

IDI_APPLICATION = 32512
TPM_RIGHTALIGN = 0x0008
TPM_BOTTOMALIGN = 0x0020
TPM_RETURNCMD = 0x0100
MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800

ID_SHOW = 1001
ID_EXIT = 1002

GWLP_WNDPROC = -4


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

if hasattr(user32, "SetWindowLongPtrW"):
    _SetWindowLong = user32.SetWindowLongPtrW
    _SetWindowLong.argtypes = [wintypes.HWND, ctypes.c_int, LONG_PTR]
    _SetWindowLong.restype = LONG_PTR
else:
    _SetWindowLong = user32.SetWindowLongW
    _SetWindowLong.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    _SetWindowLong.restype = ctypes.c_long

user32.CallWindowProcW.argtypes = [
    LONG_PTR,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.CallWindowProcW.restype = LONG_PTR


class WinTray:
    _registered_msg = None

    @classmethod
    def _tray_message_id(cls) -> int:
        if cls._registered_msg is None:
            cls._registered_msg = user32.RegisterWindowMessageW(
                "StreamRecorderTrayEvent"
            )
        return cls._registered_msg

    def __init__(self, hwnd: int, tooltip: str, on_show, on_quit):
        self.hwnd = hwnd
        self.tooltip = tooltip
        self.on_show = on_show
        self.on_quit = on_quit
        self.uID = 1
        self._msg = self._tray_message_id()
        self._added = False
        self._orig_wndproc = None
        self._wndproc_cb = None

    def _install_wndproc(self):
        @WNDPROC
        def wndproc(hWnd, uMsg, wParam, lParam):
            if uMsg == self._msg:
                if lParam in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    self.on_show()
                elif lParam == WM_RBUTTONUP:
                    self._show_menu()
                return 0
            return user32.CallWindowProcW(
                self._orig_wndproc, hWnd, uMsg, wParam, lParam
            )

        self._wndproc_cb = wndproc
        old = _SetWindowLong(
            self.hwnd,
            GWLP_WNDPROC,
            ctypes.cast(wndproc, ctypes.c_void_p).value,
        )
        if old == 0:
            err = ctypes.get_last_error()
            if err != 0:
                raise OSError(f"SetWindowLongPtrW failed: error {err}")
        self._orig_wndproc = old

    def _restore_wndproc(self):
        if self._orig_wndproc is not None:
            _SetWindowLong(self.hwnd, GWLP_WNDPROC, self._orig_wndproc)
            self._orig_wndproc = None
        self._wndproc_cb = None

    def add(self):
        if self._added:
            return
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = self.uID
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = self._msg
        nid.hIcon = user32.LoadIconW(0, IDI_APPLICATION)
        nid.szTip = self.tooltip[:127]
        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            raise OSError("Shell_NotifyIconW failed")
        self._added = True
        self._install_wndproc()

    def remove(self):
        if not self._added:
            return
        self._restore_wndproc()
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = self.uID
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        self._added = False

    def _show_menu(self):
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, MF_STRING, ID_SHOW, "Show WebcamRecorder")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, ID_EXIT, "Exit")
        pos = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pos))
        user32.SetForegroundWindow(self.hwnd)
        cmd = user32.TrackPopupMenu(
            menu,
            TPM_RIGHTALIGN | TPM_BOTTOMALIGN | TPM_RETURNCMD,
            pos.x,
            pos.y,
            0,
            self.hwnd,
            None,
        )
        # Required after TrackPopupMenu with TPM_RETURNCMD so the menu closes
        # correctly when the user clicks elsewhere (MSDN tray-menu quirk).
        user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(menu)
        if cmd == ID_SHOW:
            self.on_show()
        elif cmd == ID_EXIT:
            self.on_quit()

    def pump(self):
        pass
