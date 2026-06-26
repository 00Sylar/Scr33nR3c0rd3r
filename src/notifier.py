"""
notifier.py — Windows toast notifications (no external deps needed on Win10+)
Falls back gracefully if win10toast or winotify isn't installed.
"""

import threading


def _try_winotify(title: str, body: str) -> bool:
    try:
        from winotify import Notification, audio
        toast = Notification(
            app_id="StreamRecorder",
            title=title,
            msg=body,
            duration="short",
        )
        toast.set_audio(audio.Default, loop=False)
        toast.show()
        return True
    except Exception:
        return False


def _try_win10toast(title: str, body: str) -> bool:
    try:
        from win10toast import ToastNotifier
        t = ToastNotifier()
        t.show_toast(title, body, duration=5, threaded=True)
        return True
    except Exception:
        return False


def send_notification(title: str, body: str):
    """Fire-and-forget Windows notification. Silent on failure."""
    def _send():
        if _try_winotify(title, body):
            return
        _try_win10toast(title, body)
        # If neither works, silently skip (app still works fine)
    threading.Thread(target=_send, daemon=True).start()
