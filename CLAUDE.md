# Project: Windows Game Macro

## Overview
Windows 게임 매크로 자동화 프로젝트. Python을 사용하며 Windows Message API를 통해 게임에 입력을 전달한다.

## Tech Stack
- **Language**: Python
- **Input Method**: Windows Messages (`SendMessage`, `PostMessage` via `win32api`, `win32con`, `win32gui`)
- **Key Libraries**: `pywin32`, `ctypes`

## Architecture
- Windows Message 기반 입력 (WM_KEYDOWN, WM_KEYUP, WM_LBUTTONDOWN 등)
- 대상 윈도우 핸들(HWND)을 찾아 직접 메시지 전송
- 포그라운드 포커스 없이 백그라운드 동작 가능

## Development Guidelines
- `win32gui.FindWindow` 또는 `win32gui.FindWindowEx`로 대상 윈도우 핸들 획득
- `win32api.SendMessage` / `win32api.PostMessage`로 입력 메시지 전송
- 가상 키 코드는 `win32con.VK_*` 상수 사용
- 타이밍 제어는 `time.sleep` 사용
- 매크로 루프는 별도 스레드(`threading.Thread`)로 실행하여 중단 제어 가능하게 구성

## Common Windows Messages
| Message | 값 | 용도 |
|---|---|---|
| `WM_KEYDOWN` | 0x0100 | 키 누름 |
| `WM_KEYUP` | 0x0101 | 키 뗌 |
| `WM_LBUTTONDOWN` | 0x0201 | 마우스 좌클릭 누름 |
| `WM_LBUTTONUP` | 0x0202 | 마우스 좌클릭 뗌 |
| `WM_RBUTTONDOWN` | 0x0204 | 마우스 우클릭 누름 |
| `WM_RBUTTONUP` | 0x0205 | 마우스 우클릭 뗌 |
