import ctypes
import threading
import time

from pynput import mouse
import vgamepad as vg


SENSITIVITY = 0.003
DECAY = 0.85
DEADZONE = 2
UPDATE_HZ = 100


user32 = ctypes.windll.user32
CENTER_X = user32.GetSystemMetrics(0) // 2
CENTER_Y = user32.GetSystemMetrics(1) // 2

delta_y = 0.0
delta_lock = threading.Lock()


def on_move(x, y):
    global delta_y
    dy = y - CENTER_Y
    if dy == 0:
        return
    with delta_lock:
        delta_y += dy
    user32.SetCursorPos(CENTER_X, CENTER_Y)


mouse.Listener(on_move=on_move).start()
gamepad = vg.VX360Gamepad()
user32.SetCursorPos(CENTER_X, CENTER_Y)

cur_y = 0.0
print("Mouse-to-joystick running. Cursor is locked to screen center.")
print("Press Ctrl+C to stop.")

try:
    while True:
        with delta_lock:
            dy = delta_y
            delta_y = 0.0

        if abs(dy) < DEADZONE:
            dy = 0.0

        target = -dy * SENSITIVITY
        cur_y = cur_y * DECAY + target
        cur_y = max(-1.0, min(1.0, cur_y))

        gamepad.left_joystick_float(x_value_float=0.0, y_value_float=cur_y)
        gamepad.update()
        time.sleep(1.0 / UPDATE_HZ)
except KeyboardInterrupt:
    pass
finally:
    gamepad.reset()
    gamepad.update()
    print("Stopped.")

