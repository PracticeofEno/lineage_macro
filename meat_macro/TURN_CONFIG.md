# Turn Coordinate Config

Use `turn_config.py` in `meat_macro` to edit the root `macro_data.json`
turn coordinates by direction name.

Direction numbers:

- `1` = `north` (12 o'clock)
- `2` = `northeast`
- `3` = `east`
- `4` = `southeast`
- `5` = `south`
- `6` = `southwest`
- `7` = `west`
- `8` = `northwest`

Examples:

```powershell
python meat_macro\turn_config.py --show
python meat_macro\turn_config.py 1 654 292
python meat_macro\turn_config.py 2 816 302
python meat_macro\turn_config.py 1 654 292 2 816 302 3 770 394
python meat_macro\turn_config.py north 654 292
python meat_macro\turn_config.py northeast 816 302
python meat_macro\turn_config.py north 654 292 northeast 816 302 east 770 394
```

Interactive mode:

```powershell
python meat_macro\turn_config.py
```

Commands:

- `show`
- `1 <x> <y>`
- `1 <x> <y> 2 <x> <y> ...`
- `north <x> <y>`
- `north <x> <y> northeast <x> <y> ...`
- `northeast <x> <y>`
- `east <x> <y>`
- `southeast <x> <y>`
- `south <x> <y>`
- `southwest <x> <y>`
- `west <x> <y>`
- `northwest <x> <y>`
- `quit`
