import time
import socket
import threading
import macro
import ocr


server_socket = None
server_thread = None


def start_server():
    global server_socket
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(("0.0.0.0", 9999))
    server_socket.listen(5)

    # 접속 가능한 IP 출력
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    print(f"[서버] 소켓 열림 — 접속 IP: {local_ip}:9999")

    while True:
        try:
            conn, addr = server_socket.accept()
            print(f"[서버] 클라이언트 접속: {addr}")
            threading.Thread(target=handle_client, args=(conn,), daemon=True).start()
        except OSError:
            break


def handle_client(conn):
    with conn:
        while True:
            data = conn.recv(1024)
            if not data:
                break
            msg = data.decode("utf-8").strip()
            print(f"[서버] 수신: {msg}")
            conn.sendall(f"echo: {msg}\n".encode("utf-8"))


def connect_to_server():
    ip = input("접속할 IP 입력 (기본 127.0.0.1): ").strip() or "127.0.0.1"
    port = 9999
    try:
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.connect((ip, port))
        print(f"[클라이언트] {ip}:{port} 접속 성공. 메시지 입력 (빈 줄 엔터로 종료)")
        while True:
            msg = input("전송> ").strip()
            if not msg:
                break
            client.sendall((msg + "\n").encode("utf-8"))
            reply = client.recv(1024).decode("utf-8").strip()
            print(f"[클라이언트] 응답: {reply}")
        client.close()
    except ConnectionRefusedError:
        print(f"[클라이언트] 접속 실패: {ip}:{port} 에 서버가 없습니다.")


if __name__ == "__main__":
    macro.init_lineage_windows()
    macro.init_mouse_x_y((726, 402), (1321, 402))

    print("\n명령어: 1=소켓 서버 열기, 2=소켓 접속, q=종료")
    while True:
        cmd = input("> ").strip()
        if cmd == "q":
            break
        elif cmd == "1":
            if server_thread and server_thread.is_alive():
                print("[서버] 이미 실행 중입니다.")
            else:
                server_thread = threading.Thread(target=start_server, daemon=True)
                server_thread.start()
        elif cmd == "2":
            connect_to_server()

