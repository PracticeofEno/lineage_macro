from PIL import Image

# img = Image.open("image/exchange2.png")
img = Image.open("image/exchange_1_1.png")
cropped = img.crop((102, 292, 102 + 140, 292 + 24))
cropped.save("cropped.png")
cropped.show()

