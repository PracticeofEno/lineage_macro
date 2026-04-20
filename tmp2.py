import macro
import time

def main() -> None:
    # 3초 후 키보드 테스트 (메모장 같은 텍스트 입력 창에 포커스 두세요)
    print("3초 후 'A' 키 전송...")
    time.sleep(3)
    resp = macro._arduino_send("KP,65")   # 'A'
    print("키보드 응답:", repr(resp))

    time.sleep(1)
    print("마우스 이동 테스트...")
    resp = macro._arduino_send("RM,200,0")
    print("마우스 응답:", repr(resp))

if __name__ == "__main__":
    main()
