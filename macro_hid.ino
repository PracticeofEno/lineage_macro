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
 *   CL,<dx>,<dy>    마우스 좌클릭  (상대 이동 후 클릭)
 *   CR,<dx>,<dy>    마우스 우클릭  (상대 이동 후 클릭)
 *   MM,<dx>,<dy>    마우스 상대 이동 (RM 과 동일)
 *   RM,<dx>,<dy>    마우스 상대 이동
 *   CL              마우스 좌클릭  (이동 없이 현재 위치에서 클릭)
 *   CR              마우스 우클릭  (이동 없이 현재 위치에서 클릭)
 *   BS,<n>          백스페이스 n 회
 *
 * 응답: 각 명령 처리 후 "OK\n" 반환
 */

#include <Keyboard.h>
#include <Mouse.h>

// ── 인간형 딜레이 유틸리티 ────────────────────────────────────────────────────

// Box-Muller 변환: 정규분포 난수 생성
float gaussRand(float mean, float stddev) {
    static bool hasSpare = false;
    static float spare;
    if (hasSpare) {
        hasSpare = false;
        return mean + stddev * spare;
    }
    hasSpare = true;
    float u, v, s;
    do {
        u = (random(0, 10000) / 5000.0f) - 1.0f;
        v = (random(0, 10000) / 5000.0f) - 1.0f;
        s = u * u + v * v;
    } while (s >= 1.0f || s == 0.0f);
    s = sqrt(-2.0f * log(s) / s);
    spare = v * s;
    return mean + stddev * (u * s);
}

// 키 누름 유지 시간: 평균 65ms, 표준편차 15ms
int keyHoldMs() {
    return (int)constrain(gaussRand(65.0f, 15.0f), 35, 140);
}

// 마우스 버튼 유지 시간: 평균 75ms, 표준편차 18ms
int clickHoldMs() {
    return (int)constrain(gaussRand(75.0f, 18.0f), 40, 150);
}

// 이동→클릭 사이 대기: 혼합 분포 (outlier 7%)
//   일반: gauss(45, 12ms)
//   outlier: 200~600ms (마우스 이동 후 망설임)
int preClickMs() {
    if (random(100) < 7) {
        return (int)random(200, 600);
    }
    return (int)constrain(gaussRand(45.0f, 12.0f), 20, 120);
}

// ── Windows VK → Arduino HID 변환 ────────────────────────────────────────────
#define HID_KEY_BACKSPACE  KEY_BACKSPACE
#define HID_KEY_RIGHT_ALT  KEY_RIGHT_ALT

int vkToHid(int vk) {
    switch (vk) {
        case 0x08: return KEY_BACKSPACE;
        case 0x09: return KEY_TAB;
        case 0x0D: return KEY_RETURN;
        case 0x1B: return KEY_ESC;
        case 0x10: return KEY_LEFT_SHIFT;
        case 0x11: return KEY_LEFT_CTRL;
        case 0x12: return KEY_LEFT_ALT;
        case 0x25: return KEY_LEFT_ARROW;
        case 0x26: return KEY_UP_ARROW;
        case 0x27: return KEY_RIGHT_ARROW;
        case 0x28: return KEY_DOWN_ARROW;
        case 0x21: return KEY_PAGE_UP;
        case 0x22: return KEY_PAGE_DOWN;
        case 0x23: return KEY_END;
        case 0x24: return KEY_HOME;
        case 0x2D: return KEY_INSERT;
        case 0x2E: return KEY_DELETE;
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
        case 0x15: return KEY_RIGHT_ALT;
    }
    // 알파벳: 소문자 ASCII 로 변환 (Shift 제어는 Python 측 KD/KU)
    if (vk >= 0x41 && vk <= 0x5A) return vk + 0x20;
    if (vk >= 0x20 && vk <= 0x7E) return vk;
    return -1;
}

// ── 인간형 마우스 상대 이동 ───────────────────────────────────────────────────
// ease-in/out 사인 곡선 + 미세 jitter + 속도 가변 딜레이
void moveRel(int totalDx, int totalDy) {
    if (totalDx == 0 && totalDy == 0) return;

    float dist = sqrt((float)totalDx * totalDx + (float)totalDy * totalDy);
    int steps = (int)constrain(dist / 5.0f, 12, 60);

    int actualX = 0, actualY = 0;

    for (int i = 1; i <= steps; i++) {
        float t  = (float)i / steps;
        float et = (1.0f - cos(3.14159265f * t)) / 2.0f;  // ease-in/out

        // 이 스텝의 이상적 목표 위치
        int idealX = (i == steps) ? totalDx : (int)roundf(et * totalDx);
        int idealY = (i == steps) ? totalDy : (int)roundf(et * totalDy);

        int dx = idealX - actualX;
        int dy = idealY - actualY;

        // 마지막 스텝 제외하고 미세 jitter 추가
        if (i < steps) {
            dx += (int)gaussRand(0.0f, 0.8f);
            dy += (int)gaussRand(0.0f, 0.8f);
        }

        dx = constrain(dx, -127, 127);
        dy = constrain(dy, -127, 127);

        if (dx != 0 || dy != 0) {
            Mouse.move(dx, dy, 0);
            actualX += dx;
            actualY += dy;
        }

        // 속도: 시작/끝 느리고(16ms), 중간 빠름(5ms)
        float spd = sin(3.14159265f * t);
        delay((int)(16.0f - 11.0f * spd));
    }
}

// ── 명령 파싱 및 실행 ─────────────────────────────────────────────────────────
void processCommand(const String &cmd) {
    if (cmd.length() == 0) return;

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
        } else if (action == "KU") {
            Keyboard.release(hid);
        } else {  // KP
            Keyboard.press(hid);
            delay(keyHoldMs());
            Keyboard.release(hid);
        }

    // ── 백스페이스 n 회 ──
    } else if (action == "BS") {
        int n = rest.toInt();
        for (int i = 0; i < n; i++) {
            Keyboard.press(HID_KEY_BACKSPACE);
            delay(keyHoldMs());
            Keyboard.release(HID_KEY_BACKSPACE);
            if (i < n - 1) delay((int)constrain(gaussRand(40.0f, 10.0f), 20, 90));
        }

    // ── 마우스 상대 이동 ──
    } else if (action == "MM" || action == "RM") {
        int c2 = rest.indexOf(',');
        int dx = rest.substring(0, c2).toInt();
        int dy = rest.substring(c2 + 1).toInt();
        moveRel(dx, dy);

    // ── 마우스 좌클릭 ──
    } else if (action == "CL") {
        if (rest.length() > 0) {
            int c2 = rest.indexOf(',');
            int dx = rest.substring(0, c2).toInt();
            int dy = rest.substring(c2 + 1).toInt();
            moveRel(dx, dy);
            delay(preClickMs());
        }
        Mouse.press(MOUSE_LEFT);
        delay(clickHoldMs());
        Mouse.release(MOUSE_LEFT);

    // ── 마우스 우클릭 ──
    } else if (action == "CR") {
        if (rest.length() > 0) {
            int c2 = rest.indexOf(',');
            int dx = rest.substring(0, c2).toInt();
            int dy = rest.substring(c2 + 1).toInt();
            moveRel(dx, dy);
            delay(preClickMs());
        }
        Mouse.press(MOUSE_RIGHT);
        delay(clickHoldMs());
        Mouse.release(MOUSE_RIGHT);

    } else {
        Serial.println("ERR:UNKNOWN_CMD");
        return;
    }

    Serial.println("OK");
}

// ── setup / loop ──────────────────────────────────────────────────────────────
void setup() {
    randomSeed(analogRead(0));  // 미연결 핀 노이즈로 시드 초기화
    Serial.begin(115200);
    Keyboard.begin();
    Mouse.begin();
}

void loop() {
    if (Serial.available() > 0) {
        String line = Serial.readStringUntil('\n');
        line.trim();
        processCommand(line);
    }
}
