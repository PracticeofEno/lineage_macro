import time

import macro
import imageProcesser


def main() -> None:
    macro.init_setting("server")
    text = macro.readInputText()
    print(text)
if __name__ == "__main__":
    main()
