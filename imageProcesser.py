from PIL import Image
import numpy as np
import json
import os
import re
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))

_BASE = os.path.dirname(os.path.abspath(__file__))
_CONVERTED_DATA_PATH = os.path.join(_BASE, "converted_data.json")

with open(_CONVERTED_DATA_PATH, encoding="utf-8") as _f:
    _converted_map: dict[str, str] = json.load(_f)

def lookup(coord_string: str) -> str | None:
    return _converted_map.get(coord_string)


def crop(image: Image.Image, x: int, y: int, width: int, height: int) -> Image.Image:
    return image.crop((x, y, x + width, y + height))


def read_text(image: Image.Image, x: int, y: int, color: tuple) -> str:
    result = []
    img_width = image.width
    while x < img_width:
        matched = None
        matched_width = None
        for w in (10, 20):
            if x + w > img_width:
                continue
            s = image_to_coord_string(crop(image, x, y, w, 24), color)
            if lookup(s) is not None:
                matched = lookup(s)
                matched_width = w
                break
        if matched is None:
            break
        result.append(matched)
        x += matched_width
    return ''.join(result)


def read_line(image: Image.Image, x: int, y: int, color: tuple) -> str:
    text = read_text(image, x, y, color)
    if not text:
        text = read_text(image, x + 10, y, color)
    return text


def readExchangeNickname(screenshot: Image.Image) -> str:
    x = 107
    y, w, h = 292, 140, 24
    color = (255, 255, 255)
    best = ''
    while x >= 57:
        cropped = crop(screenshot, x, y, w, h)
        text = read_text(cropped, 0, 0, color)
        if len(text) > len(best):
            best = text
        x -= 5
    return best

def image_to_coord_string(image: Image.Image, color: tuple) -> str:
    arr = np.array(image.convert("RGB"))
    r, g, b = color
    mask = (arr[:,:,0] == r) & (arr[:,:,1] == g) & (arr[:,:,2] == b)
    ys, xs = np.where(mask)
    coords = sorted(zip(xs, ys))
    return ''.join(f"{x}{y}" for x, y in coords)
