/*
 * macro_hid.ino
 *
 * Arduino Leonardo / Micro / Pro Micro 전용 (ATmega32U4 – USB HID 지원 필수)
 *
 * Python 에서 Serial 로 명령을 보내면 실제 HID 키보드/마우스 입력을 발생시킨다.
 *
 * === 프로토콜 (개행 \n 으로 종료) ===
 *   KD,<vk>         키 누름   (Windows VK 코드 십진수)
 *   KU,<vk>         키 뗌
 *   KP,<vk>         키 누름 + 뗌
 *   CL,<x>,<y>      마우스 좌클릭  (절대 좌표 이동 후 클릭)
 *   CR,<x>,<y>      마우스 우클릭  (절대 좌표 이동 후 클릭)
 *   MM,<x>,<y>      마우스 이동    (절대 좌표)
 *   CL              마우스 좌클릭  (이동 없이 현재 위치에서 클릭)
 *   CR              마우스 우클릭  (이동 없이 현재 위치에서 클릭)
 *   LD              마우스 좌버튼 누름 (떼지 않고 유지 - 드래그용)
 *   LU              마우스 좌버튼 뗌
 *   RM,<dx>,<dy>    마우스 상대 이동
 *   BS,<n>          백스페이스 n 회
 *   INIT            마우스 커서를 (0,0) 으로 초기화
 *
 * 응답: 각 명령 처리 후 "OK\n" 반환
 *
 * === VK → HID 변환 ===
 *   ASCII 출력 문자(0x20-0x7E)는 그대로 사용
 *   F1-F12, Backspace, Enter 등 특수키는 내부 테이블로 변환
 */

#include <Keyboard.h>
#include <Mouse.h>

// 마우스 절대 좌표 추적용
static int curX = 0;
static int curY = 0;

// 타임아웃: 마지막 명령 수신 시각 (ms)
static unsigned long lastCmdTime = 0;
static bool inputsReleased = true;

// ── Windows VK 코드 → Arduino HID 키코드 변환 ──────────────────────────────
// Arduino Keyboard.h 의 특수키 상수 (Keyboard.h 참고)
#define HID_KEY_BACKSPACE  KEY_BACKSPACE   // 0xB2
#define HID_KEY_TAB        KEY_TAB         // 0xB3
#define HID_KEY_RETURN     KEY_RETURN      // 0xB0
#define HID_KEY_ESC        KEY_ESC         // 0xB1
#define HID_KEY_DELETE     KEY_DELETE      // 0xD4
#define HID_KEY_INSERT     KEY_INSERT      // 0xD1
#define HID_KEY_HOME       KEY_HOME        // 0xD2
#define HID_KEY_END        KEY_END         // 0xD5
#define HID_KEY_PAGE_UP    KEY_PAGE_UP     // 0xD3
#define HID_KEY_PAGE_DOWN  KEY_PAGE_DOWN   // 0xD6
#define HID_KEY_LEFT       KEY_LEFT_ARROW  // 0xD8
#define HID_KEY_RIGHT      KEY_RIGHT_ARROW // 0xD7
#define HID_KEY_UP         KEY_UP_ARROW    // 0xDA
#define HID_KEY_DOWN       KEY_DOWN_ARROW  // 0xD9
#define HID_KEY_CAPS_LOCK  KEY_CAPS_LOCK   // 0xC1
#define HID_KEY_LEFT_CTRL  KEY_LEFT_CTRL   // 0x80
#define HID_KEY_LEFT_SHIFT KEY_LEFT_SHIFT  // 0x81
#define HID_KEY_LEFT_ALT   KEY_LEFT_ALT    // 0x82
#define HID_KEY_RIGHT_ALT  KEY_RIGHT_ALT   // 0x86  (한/영 토글)

int vkToHid(int vk) {
    switch (vk) {
        // 제어 키
        case 0x08: return KEY_BACKSPACE;
        case 0x09: return KEY_TAB;
        case 0x0D: return KEY_RETURN;
        case 0x1B: return KEY_ESC;

        // 수정자
        case 0x10: return KEY_LEFT_SHIFT;
        case 0x11: return KEY_LEFT_CTRL;
        case 0x12: return KEY_LEFT_ALT;

        // 방향키
        case 0x25: return KEY_LEFT_ARROW;
        case 0x26: return KEY_UP_ARROW;
        case 0x27: return KEY_RIGHT_ARROW;
        case 0x28: return KEY_DOWN_ARROW;

        // 탐색
        case 0x21: return KEY_PAGE_UP;
        case 0x22: return KEY_PAGE_DOWN;
        case 0x23: return KEY_END;
        case 0x24: return KEY_HOME;
        case 0x2D: return KEY_INSERT;
        case 0x2E: return KEY_DELETE;

        // 기능키
        case 0x70: return KEY_F1;
        case 0x71: return KEY_F2;
        case 0x72: return KEY_F3;
        case 0x73: return KEY_F4;
        case 0x74: return KEY_F5;
        case 0x75: return KEY_F6;
        case 0x76: return KEY_F7;
        case 0x77: return KEY_F8;
        case 0x78: return KEY_F9;
        case 0x79: return KEY_F10;
        case 0x7A: return KEY_F11;
        case 0x7B: return KEY_F12;

        case 0x14: return KEY_CAPS_LOCK;
        case 0x15: return KEY_RIGHT_ALT;  // VK_HANGUL → 한/영 토글 (Right Alt)
    }

    // 알파벳 VK 코드(0x41-0x5A)는 소문자 ASCII로 변환
    // Keyboard.press('T') 처럼 대문자를 넘기면 Arduino가 내부적으로 Shift를 추가하기 때문.
    // Shift 제어는 Python 측 KD/KU 로만 한다.
    if (vk >= 0x41 && vk <= 0x5A) {
        return vk + 0x20;
    }

    // 나머지 ASCII 출력 문자
    if (vk >= 0x20 && vk <= 0x7E) {
        return vk;
    }
    return -1;
}
// ── 마우스 절대 이동 (내부 헬퍼) ───────────────────────────────────────────
void moveTo(int targetX, int targetY) {
    int dx = targetX - curX;
    int dy = targetY - curY;
    while (dx != 0 || dy != 0) {
        int sx = constrain(dx, -127, 127);
        int sy = constrain(dy, -127, 127);
        Mouse.move(sx, sy, 0);
        curX += sx;
        curY += sy;
        dx = targetX - curX;
        dy = targetY - curY;
        if (dx != 0 || dy != 0) delay(1);
    }
}

// ── 명령 파싱 및 실행 ────────────────────────────────────────────────────────
void processCommand(const String &cmd) {
    if (cmd.length() == 0) return;

    // 명령어 / 파라미터 분리
    int comma1 = cmd.indexOf(',');
    String action = (comma1 == -1) ? cmd : cmd.substring(0, comma1);
    String rest   = (comma1 == -1) ? ""  : cmd.substring(comma1 + 1);

    // ── 키보드 ──
    if (action == "KD" || action == "KU" || action == "KP") {
        int vk  = rest.toInt();
        int hid = vkToHid(vk);
        if (hid == -1) { Serial.println("ERR:UNKNOWN_VK"); return; }

        if (action == "KD") {
            Keyboard.press(hid);
            delay(30);
        } else if (action == "KU") {
            Keyboard.release(hid);
            delay(30);
        } else {  // KP
            Keyboard.press(hid);
            delay(30);
            Keyboard.release(hid);
            delay(30);
        }

    // ── 백스페이스 n 회 ──
    } else if (action == "BS") {
        int n = rest.toInt();
        for (int i = 0; i < n; i++) {
            Keyboard.press(HID_KEY_BACKSPACE);
            delay(30);
            Keyboard.release(HID_KEY_BACKSPACE);
            delay(30);
        }

    // ── 마우스 이동 ──
    } else if (action == "MM") {
        int c2 = rest.indexOf(',');
        int x  = rest.substring(0, c2).toInt();
        int y  = rest.substring(c2 + 1).toInt();
        moveTo(x, y);

    // ── 마우스 좌클릭 (좌표 있으면 이동 후 클릭, 없으면 현재 위치) ──
    } else if (action == "CL") {
        if (rest.length() > 0) {
            int c2 = rest.indexOf(',');
            int x  = rest.substring(0, c2).toInt();
            int y  = rest.substring(c2 + 1).toInt();
            moveTo(x, y);
            delay(30);
        }
        Mouse.press(MOUSE_LEFT);
        delay(50);
        Mouse.release(MOUSE_LEFT);

    // ── 마우스 우클릭 (좌표 있으면 이동 후 클릭, 없으면 현재 위치) ──
    } else if (action == "CR") {
        if (rest.length() > 0) {
            int c2 = rest.indexOf(',');
            int x  = rest.substring(0, c2).toInt();
            int y  = rest.substring(c2 + 1).toInt();
            moveTo(x, y);
            delay(30);
        }
        Mouse.press(MOUSE_RIGHT);
        delay(50);
        Mouse.release(MOUSE_RIGHT);

    // ── 마우스 좌버튼 누름 유지 (드래그 시작) ──
    } else if (action == "LD") {
        Mouse.press(MOUSE_LEFT);
        delay(10);

    // ── 마우스 좌버튼 뗌 (드래그 종료) ──
    } else if (action == "LU") {
        Mouse.release(MOUSE_LEFT);
        delay(10);

    // ── 마우스 상대 이동 ──
    } else if (action == "RM") {
        int c2 = rest.indexOf(',');
        int dx = rest.substring(0, c2).toInt();
        int dy = rest.substring(c2 + 1).toInt();
        int steps = max(abs(dx), abs(dy));
        if (steps == 0) steps = 1;
        for (int i = 1; i <= steps; i++) {
            int mx = (int)((long)dx * i / steps) - (int)((long)dx * (i - 1) / steps);
            int my = (int)((long)dy * i / steps) - (int)((long)dy * (i - 1) / steps);
            Mouse.move(mx, my, 0);
            curX += mx;
            curY += my;
            if (i < steps) delay(1);
        }

    // ── 커서 초기화 (좌상단 (0,0)) ──
    } else if (action == "INIT") {
        // 충분히 큰 음수로 반복 이동하여 (0,0) 근방으로 강제 이동
        for (int i = 0; i < 100; i++) Mouse.move(-127, -127, 0);
        curX = 0;
        curY = 0;

    } else {
        Serial.println("ERR:UNKNOWN_CMD");
        return;
    }

    Serial.println("OK");
}

// ── setup / loop ─────────────────────────────────────────────────────────────
void setup() {
    Serial.begin(115200);
    Keyboard.begin();
    Mouse.begin();
}

void loop() {
    if (Serial.available() > 0) {
        String line = Serial.readStringUntil('\n');
        line.trim();
        lastCmdTime = millis();
        inputsReleased = false;
        processCommand(line);
    }

    // 3초간 명령이 없으면 눌린 키/버튼 전부 해제 (Python 크래시 등 안전장치)
    if (!inputsReleased && (millis() - lastCmdTime > 3000)) {
        Keyboard.releaseAll();
        Mouse.release(MOUSE_LEFT);
        Mouse.release(MOUSE_RIGHT);
        Mouse.release(MOUSE_MIDDLE);
        inputsReleased = true;
    }
}
