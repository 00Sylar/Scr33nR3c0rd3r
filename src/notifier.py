"""
notifier.py — Windows toast notifications (no external deps needed on Win10+)
Falls back gracefully if win10toast or winotify isn't installed.
"""

import threading


def _try_winotify(title: str, body: str, secs: int) -> bool:
    try:
        from winotify import Notification, audio
        # winotify only exposes "short" (~5 s) / "long" (~25 s); Windows itself
        # controls the exact lifetime, so the numeric seconds are a hint.
        toast = Notification(
            app_id="StreamRecorder",
            title=title,
            msg=body,
            duration="long" if secs >= 8 else "short",
        )
        toast.set_audio(audio.Default, loop=False)
        toast.show()
        return True
    except Exception:
        return False


def _try_win10toast(title: str, body: str, secs: int) -> bool:
    try:
        from win10toast import ToastNotifier
        t = ToastNotifier()
        t.show_toast(title, body, duration=max(1, int(secs)), threaded=True)
        return True
    except Exception:
        return False


def send_notification(title: str, body: str, duration_secs: int = 5):
    """Fire-and-forget Windows notification. Silent on failure.

    `duration_secs` is a hint: win10toast honors it directly; winotify maps it
    to its short/long buckets and Windows may still override the final lifetime."""
    def _send():
        if _try_winotify(title, body, duration_secs):
            return
        _try_win10toast(title, body, duration_secs)
        # If neither works, silently skip (app still works fine)
    threading.Thread(target=_send, daemon=True).start()
