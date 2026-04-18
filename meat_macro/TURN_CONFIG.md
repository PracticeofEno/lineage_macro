# Turn Coordinate Config

Use `turn_config.py` in `meat_macro` to edit the root `macro_data.json`
turn coordinates by direction name.

Examples:

```powershell
python meat_macro\turn_config.py --show
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
