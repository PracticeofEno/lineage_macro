import macro

macro.init_lineage_windows()
macro.init_mouse_x_y()
img = macro.screenshot(hwnd=macro.lineage2_hwnd)
adena = macro.readMp(img=img)
print(f"현재 아데나: {adena}")
