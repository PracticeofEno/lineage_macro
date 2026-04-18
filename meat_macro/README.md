# Meat Macro

This project lives only inside the `meat_macro` folder.
It does not require changes to any existing project files.

## What It Does

For each target window:

0. Move the matched window to `(0, 0)` first.
1. Use one fixed start point.
2. Drag from that start point to end point 1, then release.
3. Press `1`.
4. Press `Enter`.
5. Repeat the same sequence for end points 2, 3, and 4.
6. Repeat the whole cycle every 30 minutes by default.

Supported targets:

- `server`
- `client`
- `all` (server, then client)

## Files

- `meat_macro.py`: coordinate setup and macro runner
- `meat_macro_config.json`: saved coordinates and schedule
- `meat_macro_proxy.py`: serial-to-TCP proxy for this macro
- `meat_macro_hid.ino`: Arduino HID sketch with drag support

## Setup

1. Flash `meat_macro_hid.ino` to an Arduino Leonardo / Micro / Pro Micro.
2. Start the proxy:

```powershell
python meat_macro\meat_macro_proxy.py --serial-port COM11
```

3. Set coordinates for each window:

```powershell
python meat_macro\meat_macro.py
```

Then enter absolute screen coordinates directly:

- `set start server 1191 190`
- `set end1 server 638 351`
- `set end2 server 640 360`
- `set end3 server 650 372`
- `set end4 server 660 384`
- `set start client 1191 190`
- `set end1 client 638 351`
- `set end2 client 640 360`
- `set end3 client 650 372`
- `set end4 client 660 384`

## Run

Interactive:

```powershell
python meat_macro\meat_macro.py
```

One-shot:

```powershell
python meat_macro\meat_macro.py --set start --target server --x 1191 --y 190
python meat_macro\meat_macro.py --set end1 --target server --x 638 --y 351
python meat_macro\meat_macro.py --run server
python meat_macro\meat_macro.py --run client
python meat_macro\meat_macro.py --run all
```

Scheduled loop:

```powershell
python meat_macro\meat_macro.py --loop server
python meat_macro\meat_macro.py --loop client
python meat_macro\meat_macro.py --loop all
```

Interactive loop commands:

- `loop start server`
- `loop start client`
- `loop start all`
- `loop stop`

## Notes

- Before each run, the matched `server` or `client` window is moved to `(0, 0)` automatically.
- The start point is fixed per window and each window has 4 separate end points.
- Coordinates are absolute screen positions.
- Enter coordinates based on the layout after the window has been moved to `(0, 0)`.
- The target window title is matched by prefix from `meat_macro_config.json`.
- This macro assumes the target windows are on the primary monitor.
