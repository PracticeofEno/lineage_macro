import win32api
import win32con

# 두벌식 표준 자판: 단일 자모 → (물리키, shift)
JAMO_KEY_MAP = {
    'ㄱ': ('R', False), 'ㄲ': ('R', True),  'ㄴ': ('S', False),
    'ㄷ': ('E', False), 'ㄸ': ('E', True),  'ㄹ': ('F', False),
    'ㅁ': ('A', False), 'ㅂ': ('Q', False), 'ㅃ': ('Q', True),
    'ㅅ': ('T', False), 'ㅆ': ('T', True),  'ㅇ': ('D', False),
    'ㅈ': ('W', False), 'ㅉ': ('W', True),  'ㅊ': ('C', False),
    'ㅋ': ('Z', False), 'ㅌ': ('X', False), 'ㅍ': ('V', False),
    'ㅎ': ('G', False),
    'ㅏ': ('K', False), 'ㅐ': ('O', False), 'ㅑ': ('I', False),
    'ㅒ': ('O', True),  'ㅓ': ('J', False), 'ㅔ': ('P', False),
    'ㅕ': ('U', False), 'ㅖ': ('P', True),  'ㅗ': ('H', False),
    'ㅛ': ('Y', False), 'ㅜ': ('N', False), 'ㅠ': ('B', False),
    'ㅡ': ('M', False), 'ㅣ': ('L', False),
}

# 복합 자모 → 단일 자모 시퀀스
COMPOUND_JAMO = {
    'ㄳ': ['ㄱ', 'ㅅ'], 'ㄵ': ['ㄴ', 'ㅈ'], 'ㄶ': ['ㄴ', 'ㅎ'],
    'ㄺ': ['ㄹ', 'ㄱ'], 'ㄻ': ['ㄹ', 'ㅁ'], 'ㄼ': ['ㄹ', 'ㅂ'],
    'ㄽ': ['ㄹ', 'ㅅ'], 'ㄾ': ['ㄹ', 'ㅌ'], 'ㄿ': ['ㄹ', 'ㅍ'],
    'ㅀ': ['ㄹ', 'ㅎ'], 'ㅄ': ['ㅂ', 'ㅅ'],
    'ㅘ': ['ㅗ', 'ㅏ'], 'ㅙ': ['ㅗ', 'ㅐ'], 'ㅚ': ['ㅗ', 'ㅣ'],
    'ㅝ': ['ㅜ', 'ㅓ'], 'ㅞ': ['ㅜ', 'ㅔ'], 'ㅟ': ['ㅜ', 'ㅣ'],
    'ㅢ': ['ㅡ', 'ㅣ'],
}

CHOSEONG  = ['ㄱ','ㄲ','ㄴ','ㄷ','ㄸ','ㄹ','ㅁ','ㅂ','ㅃ','ㅅ','ㅆ','ㅇ','ㅈ','ㅉ','ㅊ','ㅋ','ㅌ','ㅍ','ㅎ']
JUNGSEONG = ['ㅏ','ㅐ','ㅑ','ㅒ','ㅓ','ㅔ','ㅕ','ㅖ','ㅗ','ㅘ','ㅙ','ㅚ','ㅛ','ㅜ','ㅝ','ㅞ','ㅟ','ㅠ','ㅡ','ㅢ','ㅣ']
JONGSEONG = ['','ㄱ','ㄲ','ㄳ','ㄴ','ㄵ','ㄶ','ㄷ','ㄹ','ㄺ','ㄻ','ㄼ','ㄽ','ㄾ','ㄿ','ㅀ','ㅁ','ㅂ','ㅄ','ㅅ','ㅆ','ㅇ','ㅈ','ㅊ','ㅋ','ㅌ','ㅍ','ㅎ']


def decompose_hangul(ch: str):
    code = ord(ch) - 0xAC00
    return (
        CHOSEONG[code // (21 * 28)],
        JUNGSEONG[(code % (21 * 28)) // 28],
        JONGSEONG[code % 28],
    )


def press_key(vk: int, shift: bool = False):
    import time
    if shift:
        win32api.keybd_event(win32con.VK_SHIFT, 0, 0, 0)
    win32api.keybd_event(vk, 0, 0, 0)
    time.sleep(0.01)
    win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
    if shift:
        win32api.keybd_event(win32con.VK_SHIFT, 0, win32con.KEYEVENTF_KEYUP, 0)


def send_jamo(jamo: str):
    if jamo in COMPOUND_JAMO:
        for j in COMPOUND_JAMO[jamo]:
            send_jamo(j)
        return
    key, shift = JAMO_KEY_MAP[jamo]
    press_key(ord(key), shift)


def send_char(ch: str, hwnd: int):
    if ord(ch) <= 0x7F:
        win32api.PostMessage(hwnd, win32con.WM_CHAR, ord(ch), 0)
    else:
        cho, jung, jong = decompose_hangul(ch)
        send_jamo(cho)
        send_jamo(jung)
        if jong:
            send_jamo(jong)
        # IME 조합 버퍼 강제 확정 (없으면 다음 자모와 합쳐짐)
        press_key(win32con.VK_RIGHT)
