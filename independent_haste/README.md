# independent_haste 사용법

이 폴더는 기존 `server.py`와 비슷한 명령 방식으로 사용하지만, server/client는 서로 통신하지 않고 각각 독립적으로 동작합니다.

## 실행

PowerShell에서:

```powershell
cd C:\Users\서주희\Desktop\Dev\lineage_macro\independent_haste
python independent_haste.py
```

실행하면 바로 매크로가 시작되는 것이 아니라 아래 명령 대기 상태가 됩니다.

```text
명령어: q=종료, 1=independent haste 시작, 2=independent haste 중지
>
```

명령:

- `1`: server/client 독립 매크로 시작
- `2`: server/client 독립 매크로 중지
- `q`: 종료

로그는 같은 콘솔에 아래처럼 같이 출력됩니다.

```text
server | ...
client | ...
```

## 개별 실행

필요할 때만 직접 역할을 지정해서 따로 실행할 수 있습니다.

```powershell
python independent_haste.py server
python independent_haste.py client
```

## 설정

독립 매크로 설정:

```text
independent_haste_config.json
```

주요 설정:

```json
{
  "same_nickname_turn_seconds": 0,
  "start_arduino_proxy": false,
  "status_interval": 3.0,
  "start_delay_seconds": 2.0,
  "python_executable": ""
}
```

- `same_nickname_turn_seconds`: 같은 좌표에서 같은 닉네임이 지정 시간 이상 감지되면 `macro_data.json`의 `direction_change_nicknames`에 자동 추가하고 방향전환합니다. `0`이면 비활성화입니다.
- 같은 값은 교환창에도 적용됩니다. 같은 거래창 닉네임이 지정 시간 이상 유지되면 해당 닉네임을 자동 추가하고, ESC로 취소한 뒤 다른 방향으로 전환합니다.
- `start_arduino_proxy`: `true`면 시작 명령 `1`을 눌렀을 때 이 폴더의 `arduino_proxy.py`도 같이 실행합니다.
- `status_interval`: 상태 로그 출력 간격입니다.
- `start_delay_seconds`: server 실행 후 client 실행까지 기다리는 시간입니다.
- `python_executable`: 비워두면 현재 Python을 사용합니다.

좌표, 방향, 가격 설정은 이 폴더 안의 `macro_data.json`에서 바꿉니다.

교환창은 픽셀 변화 확인 없이 슬롯 밝기가 120을 넘으면 OK합니다.

## 실행 명령 확인

실제로 실행하지 않고 어떤 명령이 실행될지만 확인하려면:

```powershell
python independent_haste.py --dry-run
```
