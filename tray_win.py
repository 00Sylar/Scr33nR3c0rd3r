"""
tray_win.py — Windows system tray icon (ctypes, no extra deps)
"""

import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32

NIM_ADD = 0x00000000
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004

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


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


class WinTray:
    _registered_msg = None

    @classmethod
    def _tray_message_id(cls) -> int:
        if cls._registered_msg is None:
            cls._registered_msg = user32.RegisterWindowMessageW("StreamRecorderTrayEvent")
        return cls._registered_msg

    def __init__(self, hwnd: int, tooltip: str, on_show, on_quit):
        self.hwnd = hwnd
        self.tooltip = tooltip
        self.on_show = on_show
        self.on_quit = on_quit
        self.uID = 1
        self._msg = self._tray_message_id()
        self._added = False

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

    def remove(self):
        if not self._added:
            return
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
        user32.DestroyMenu(menu)
        if cmd == ID_SHOW:
            self.on_show()
        elif cmd == ID_EXIT:
            self.on_quit()

    def pump(self):
        if not self._added:
            return
        msg = MSG()
        PM_REMOVE = 0x0001
        mid = self._msg
        while user32.PeekMessageW(ctypes.byref(msg), self.hwnd, mid, mid, PM_REMOVE):
            if msg.lParam == WM_LBUTTONDBLCLK:
                self.on_show()
            elif msg.lParam == WM_RBUTTONUP:
                self._show_menu()
