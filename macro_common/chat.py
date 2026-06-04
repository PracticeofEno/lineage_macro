from __future__ import annotations

import win32con

try:
    from tools import hangul
except ImportError:  # pragma: no cover - used when tools/ is added directly to sys.path.
    import hangul

VK_HANGUL = 0x15
CHAT_INPUT_ENABLED = False

_SHIFT_CHAR_MAP = {
    "!": "1",
    "@": "2",
    "#": "3",
    "$": "4",
    "%": "5",
    "^": "6",
    "&": "7",
    "*": "8",
    "(": "9",
    ")": "0",
    "_": "-",
    "+": "=",
    "{": "[",
    "}": "]",
    "|": "\\",
    ":": ";",
    '"': "'",
    "<": ",",
    ">": ".",
    "?": "/",
    "~": "`",
}


class ChatTyper:
    def __init__(self, proxy, starts_in_korean_mode: bool, send_enter: bool):
        self._proxy = proxy
        self._starts_in_korean_mode = starts_in_korean_mode
        self._send_enter = send_enter

    def type_text(self, text: str) -> None:
        if not CHAT_INPUT_ENABLED:
            return

        current_korean_mode = self._starts_in_korean_mode

        def set_mode(need_korean: bool) -> None:
            nonlocal current_korean_mode
            if current_korean_mode == need_korean:
                return
            self._proxy.key_press(VK_HANGUL)
            current_korean_mode = need_korean

        for ch in text:
            is_korean = "\uAC00" <= ch <= "\uD7A3"

            if ch == " ":
                self._proxy.key_press(win32con.VK_SPACE)
            elif is_korean:
                set_mode(True)
                self._type_hangul(ch)
            elif ch.isalpha():
                set_mode(False)
                vk = ord(ch.upper())
                if ch.isupper():
                    self._proxy.key_down(win32con.VK_SHIFT)
                    self._proxy.key_press(vk)
                    self._proxy.key_up(win32con.VK_SHIFT)
                else:
                    self._proxy.key_press(vk)
            elif ch.isdigit():
                set_mode(False)
                self._proxy.key_press(ord(ch))
            elif ch in _SHIFT_CHAR_MAP:
                set_mode(False)
                self._proxy.key_down(win32con.VK_SHIFT)
                self._proxy.key_press(ord(_SHIFT_CHAR_MAP[ch]))
                self._proxy.key_up(win32con.VK_SHIFT)
            else:
                set_mode(False)
                self._proxy.key_press(ord(ch))

        if current_korean_mode != self._starts_in_korean_mode:
            self._proxy.key_press(VK_HANGUL)

        if self._send_enter:
            self._proxy.key_press(win32con.VK_RETURN)

    def _type_hangul(self, ch: str) -> None:
        cho, jung, jong = hangul.decompose_hangul(ch)
        self._type_jamo(cho)
        self._type_jamo(jung)
        if jong:
            self._type_jamo(jong)
        self._proxy.key_press(win32con.VK_RIGHT)

    def _type_jamo(self, jamo: str) -> None:
        if jamo in hangul.COMPOUND_JAMO:
            for part in hangul.COMPOUND_JAMO[jamo]:
                self._type_jamo(part)
            return

        key, shift = hangul.JAMO_KEY_MAP[jamo]
        vk = ord(key)
        if shift:
            self._proxy.key_down(win32con.VK_SHIFT)
        self._proxy.key_press(vk)
        if shift:
            self._proxy.key_up(win32con.VK_SHIFT)
