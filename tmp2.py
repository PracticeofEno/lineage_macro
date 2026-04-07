import time
import win32gui
import macro

macro.init_lineage_windows()
win32gui.SetForegroundWindow(macro.lineage1_hwnd)

macro.turn_north()
time.sleep(1)
macro.turn_northeast()
time.sleep(1)
macro.turn_east()
time.sleep(1)
macro.turn_southeast()
time.sleep(1)
macro.turn_south()
time.sleep(1)
macro.turn_southwest()
time.sleep(1)
macro.turn_west()
time.sleep(1)
macro.turn_northwest()
