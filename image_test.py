from PIL import Image
import imageProcesser
import ocr

image = Image.open("image/screenshot.png")
cropped = imageProcesser.crop(image, 155, 583, 50, 15)
cropped.show()
# cropped.save("image/crop.png")
# result = ocr.ocr_image(cropped)
# print(result)
