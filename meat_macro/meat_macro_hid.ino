/*
 * meat_macro_hid.ino
 *
 * Arduino Leonardo / Micro / Pro Micro only.
 * Commands:
 *   INIT        reset the cursor to top-left and sync internal position
 *   MM,x,y      move mouse to absolute screen coordinate
 *   LD          left mouse down
 *   LU          left mouse up
 *   KP,vk       key press
 *
 * Response: "OK\n" for every successful command.
 */

#include <Keyboard.h>
#include <Mouse.h>

static int curX = 0;
static int curY = 0;
static const unsigned long IDLE_RELEASE_MS = 3000;
static unsigned long lastCommandAtMs = 0;
static bool idleReleaseSent = false;

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
    case 0x7A: return KEY_F11;
    case 0x7B: return KEY_F12;
  }

  if (vk >= 0x41 && vk <= 0x5A) {
    return vk + 0x20;
  }

  if (vk >= 0x20 && vk <= 0x7E) {
    return vk;
  }

  return -1;
}

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
    if (dx != 0 || dy != 0) {
      delay(1);
    }
  }
}

void releaseAllInputs() {
  Keyboard.releaseAll();
  Mouse.release(MOUSE_LEFT);
  Mouse.release(MOUSE_RIGHT);
  Mouse.release(MOUSE_MIDDLE);
}

void releaseInputsAfterIdle() {
  if (!idleReleaseSent && millis() - lastCommandAtMs >= IDLE_RELEASE_MS) {
    releaseAllInputs();
    idleReleaseSent = true;
  }
}

void processCommand(const String &cmd) {
  if (cmd.length() == 0) {
    return;
  }

  int comma1 = cmd.indexOf(',');
  String action = (comma1 == -1) ? cmd : cmd.substring(0, comma1);
  String rest = (comma1 == -1) ? "" : cmd.substring(comma1 + 1);

  if (action == "INIT") {
    for (int i = 0; i < 100; i++) {
      Mouse.move(-127, -127, 0);
    }
    curX = 0;
    curY = 0;
  } else if (action == "MM") {
    int comma2 = rest.indexOf(',');
    if (comma2 == -1) {
      Serial.println("ERR:BAD_ARGS");
      return;
    }
    int x = rest.substring(0, comma2).toInt();
    int y = rest.substring(comma2 + 1).toInt();
    moveTo(x, y);
  } else if (action == "LD") {
    Mouse.press(MOUSE_LEFT);
  } else if (action == "LU") {
    Mouse.release(MOUSE_LEFT);
  } else if (action == "KP") {
    int vk = rest.toInt();
    int hid = vkToHid(vk);
    if (hid == -1) {
      Serial.println("ERR:UNKNOWN_VK");
      return;
    }
    Keyboard.press(hid);
    delay(30);
    Keyboard.release(hid);
  } else {
    Serial.println("ERR:UNKNOWN_CMD");
    return;
  }

  Serial.println("OK");
}

void setup() {
  Serial.begin(115200);
  Keyboard.begin();
  Mouse.begin();
  lastCommandAtMs = millis();
}

void loop() {
  if (Serial.available() > 0) {
    String line = Serial.readStringUntil('\n');
    line.trim();
    processCommand(line);
    lastCommandAtMs = millis();
    idleReleaseSent = false;
  }
  releaseInputsAfterIdle();
}
