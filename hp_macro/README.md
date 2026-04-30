# HP Macro

현재 프로젝트의 `macro.py`와 `converted_data.json` OCR 데이터를 재사용해서 HP를 읽고, HP가 50% 미만이면 F5를 1.5초 동안 누르는 단독 매크로입니다. MP가 30% 미만이면 F8을 누르고, F8 쿨타임은 600초입니다.

좌표와 캡처는 기본적으로 화면 절대좌표를 그대로 사용합니다.

## 실행

1. `arduino_proxy.py`를 먼저 실행합니다.
2. 리니지 창을 켠 상태에서 아래 명령을 실행합니다.

```powershell
python hp_macro\hp_macro.py
```

기본 대상 창은 `client`입니다. `hp_macro/config.json`의 `window_title` 값으로 바꿀 수 있습니다.

서버 창에서 실행하려면:

```powershell
python hp_macro\hp_macro.py --title server
```

클라이언트 창을 명시하려면:

```powershell
python hp_macro\hp_macro.py --title client
```

HP 퍼센트 기준을 실행 시 바꾸려면:

```powershell
python hp_macro\hp_macro.py --percent-threshold 40
```

HP가 읽히는지만 한 번 확인하려면:

```powershell
python hp_macro\hp_macro.py --once
```

HP 숫자 RGB 후보를 확인하려면:

```powershell
python hp_macro\hp_macro.py --sample-colors
```

## 설정

기본 설정은 `hp_macro/config.json`에 있습니다.

- `window_title`: 기본 대상 창 제목입니다. `client` 또는 `server`처럼 지정합니다.
- `hp_percent_threshold`: 현재 HP / 최대 HP * 100 값이 이 값 미만이면 F5를 누릅니다. 기본값은 `50.0`입니다.
- `mp_percent_threshold`: 현재 MP / 최대 MP * 100 값이 이 값 미만이면 F8을 누릅니다. 기본값은 `30.0`입니다.
- `f5_hold_seconds`: F5를 누르고 있을 시간입니다. 기본값은 `1.5`초입니다.
- `f8_cooldown_seconds`: F8 발동 쿨타임입니다. 기본값은 `600.0`초입니다.
- `trigger_cooldown_seconds`: 연속 발동 방지 시간입니다.
- `hp_read.coordinate_mode`: `x`, `y`가 어떤 기준인지 정합니다. 기본값은 `screen`이라 화면 절대좌표입니다.
- `hp_read.capture_mode`: HP 영역을 어디서 캡처할지 정합니다. 기본값은 `screen`이라 화면 절대좌표에서 직접 캡처합니다.
- `hp_read.x`, `hp_read.y`, `hp_read.width`, `hp_read.height`: HP 숫자 OCR 영역입니다.
- `hp_read.color_rgb`: HP 숫자 색상입니다.
- `hp_read.text_x_offsets`, `hp_read.text_y_offsets`: OCR 영역 안에서 실제 숫자 시작 위치를 찾기 위한 내부 스캔 오프셋입니다.
- `mp_read`: 기존 `lineage_macro`의 `macro.readMp()`와 같은 MP OCR 좌표와 색상을 사용합니다. 기본값은 창 내부 좌표 `x=976`, `y=96`, 색상 `(204, 227, 255)`입니다.

현재 기본 조합은 `coordinate_mode=screen`, `capture_mode=screen`입니다. 즉 좌표값은 화면 절대좌표로 넣고, 실제 읽기도 화면 절대좌표에서 직접 수행합니다.
