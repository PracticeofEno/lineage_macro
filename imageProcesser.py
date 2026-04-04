from PIL import Image

def crop(image: Image.Image, x: int, y: int, width: int, height: int) -> Image.Image:
    return image.crop((x, y, x + width, y + height))
