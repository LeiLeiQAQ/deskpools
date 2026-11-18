"""Windows notification-area menu, backed by pystray."""
import queue
from PIL import Image
import pystray
from . import config


class Tray:
    def __init__(self):
        self.pending = queue.SimpleQueue()
        def action(name):
            return lambda icon, item: self.pending.put(name)
        image = Image.open(config.ICON_PATH).convert('RGBA')
        self.icon = pystray.Icon('DeskPools', image, config.PRODUCT_NAME, pystray.Menu(
            pystray.MenuItem('暂停 / 恢复', action('pause'), default=True),
            pystray.MenuItem('设置', action('settings')),
            pystray.MenuItem('退出并恢复桌面', action('quit'))))
        self.icon.run_detached()

    def close(self):
        self.icon.stop()
