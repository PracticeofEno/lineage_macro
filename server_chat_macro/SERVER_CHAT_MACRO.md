# Server Chat Macro

`server_chat_macro.py` is a separate chat-only macro for the `server` window.
It does not touch pickup, exchange, mouse, or existing project files.

## Requirements

1. Flash the existing Arduino HID sketch from `macro_hid.ino`.
2. Run `arduino_proxy.py` so the local proxy listens on `127.0.0.1:9998`.
3. Make sure the game window title starts with `server`.

## Usage

Interactive mode:

```powershell
python server_chat_macro\server_chat_macro.py
```

One-shot send:

```powershell
python server_chat_macro\server_chat_macro.py --send "안녕하세요."
```

Preset send:

```powershell
python server_chat_macro\server_chat_macro.py --preset 1
```

## Interactive Commands

- `status`
- `preset`
- `preset <n>`
- `loop start [seconds]`
- `loop stop`
- `reload`
- `send <text>`
- `quit`

Any input that does not match a command is sent to the `server` window immediately.

## Config

Edit `server_chat_macro\server_chat_macro.json`.

- `window_title_prefix`: target window title prefix
- `proxy_host`, `proxy_port`: Arduino proxy address
- `starts_in_korean_mode`: initial IME mode assumption
- `send_enter`: press Enter after typing
- `default_interval_seconds`: loop interval
- `messages`: preset chat lines
