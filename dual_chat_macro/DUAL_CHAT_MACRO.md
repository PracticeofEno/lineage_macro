# Dual Chat Macro

`dual_chat_macro.py` is a separate chat-only macro that alternates between the `server` and `client` windows.
It does not touch pickup, exchange, mouse coordinates, or the existing project scripts.

## Requirements

1. Flash the existing Arduino HID sketch from `macro_hid.ino`.
2. Run `arduino_proxy.py` so the local proxy listens on `127.0.0.1:9998`.
3. Make sure the game window titles start with `server` and `client`.

## Usage

Interactive mode:

```powershell
python dual_chat_macro\dual_chat_macro.py
```

One-shot send:

```powershell
python dual_chat_macro\dual_chat_macro.py --role server --send "안녕하세요"
python dual_chat_macro\dual_chat_macro.py --role client --send "클라이언트 문구"
```

Preset send:

```powershell
python dual_chat_macro\dual_chat_macro.py --role server --preset 1
python dual_chat_macro\dual_chat_macro.py --role client --preset 2
```

## Interactive Commands

- `status`
- `preset server`
- `preset client`
- `preset server <n>`
- `preset client <n>`
- `loop start [seconds]`
- `loop stop`
- `reload`
- `send server <text>`
- `send client <text>`
- `server <text>`
- `client <text>`
- `quit`

## Config

Edit `dual_chat_macro\dual_chat_macro.json`.

- `cycle_interval_seconds`: wait time after each send
- `switch_delay_seconds`: delay after focusing a window before typing
- `post_send_delay_seconds`: delay after Enter before switching to the other window
- `order`: send order, usually `["server", "client"]`
- `targets.server.messages`: server-only chat lines
- `targets.client.messages`: client-only chat lines
- `targets.<role>.window_title_prefix`: target window title prefix
