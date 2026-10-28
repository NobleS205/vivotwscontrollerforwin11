"""
耳机控制台（Windows）启动入口 —— vivo / iQOO + 华为 / HONOR 双品牌。

用法：
    python app.py                 # 托盘常驻；自动识别已连接的耳机，否则弹面板
    python app.py --demo          # vivo 演示模式（无需耳机、无需 Windows）
    python app.py --demo=huawei   # 华为 FreeBuds Pro 5 演示模式

热键：
    Ctrl + Alt + V           唤出 / 收起浮窗
    Win + A                  系统控制中心弹出后，浮窗自动叠加在其上方（可关闭）

自动识别（v3.17）：
    后台每 3s 读一次 Windows 蓝牙设备表，发现受支持的耳机进入「已连接」状态
    就自动接管并（按设置）弹出面板，无需手动选设备。
"""

from __future__ import annotations

import sys
import threading

from PySide6.QtCore import QSettings, QTimer, Qt
from PySide6.QtWidgets import QApplication

from pods.autoconnect import AutoConnectManager
from pods.detect import HUAWEI, VIVO
from pods.hub import DeviceInfo, PodsHub
from ui.ccwatch import ControlCenterWatcher
from ui.flyout import FlyoutWindow
from ui.hotkey import HotkeyManager, IS_WINDOWS
from ui.tray import TrayController, build_app_icon


# 注意：QSettings 的组织/应用名保持 "VivoPodsCC" 不变 —— 它决定配置文件路径，
# 改了会丢掉用户的「上次设备 / 深色模式 / 自动识别」等已有设置。
SETTINGS_ORG = "VivoPodsCC"
SETTINGS_APP = "config"


class VivoPodsApp:
    def __init__(self, argv: list[str]) -> None:
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
        self.qt = QApplication(argv)
        self.qt.setApplicationName("VivoPods Controller")
        self.qt.setQuitOnLastWindowClosed(False)
        self.qt.aboutToQuit.connect(self.shutdown)
        self.settings = QSettings(
            QSettings.IniFormat, QSettings.UserScope, SETTINGS_ORG, SETTINGS_APP
        )

        # 多品牌统一控制器：内部按型号路由到 vivo 私有协议 / 华为上游驱动
        self.ctrl = PodsHub()
        self.flyout = FlyoutWindow(self.ctrl, self.settings)
        self.flyout.hotkeys = None

        self.tray = TrayController(self.qt, self.flyout, self.ctrl, self.settings)
        self.tray.show()

        # 全局热键 / Win+A 跟随
        self.hotkeys = HotkeyManager()
        self.hotkeys.set_follow_win_a(
            self.settings.value("follow_win_a", True, type=bool)
        )
        self.hotkeys.win_a_pressed.connect(self.flyout.show_after_win_a)
        self.hotkeys.toggle_requested.connect(self.flyout.toggle_flyout)
        self.flyout.hotkeys = self.hotkeys
        self.flyout.quit_callback = self.shutdown
        self.tray.quit_callback = self.shutdown
        # 任务 C2：主题切换时刷新托盘图标颜色
        self.flyout.tray_icon_callback = self.tray.refresh_tray_icon
        if IS_WINDOWS:
            self._install_hotkeys()

        # 系统控制中心跟随：打开 → 叠加在其正上方；关闭 → 同步收起
        self.cc_watch = ControlCenterWatcher()
        self.cc_watch.log_enabled = self.settings.value(
            "ccwatch_log", True, type=bool)
        self.cc_watch.opened.connect(self.flyout.show_for_control_center)
        self.cc_watch.closed.connect(self.flyout.hide_for_control_center)
        self.flyout.cc_watch_callback = self._set_cc_watch
        self.flyout.cc_log_callback = self._set_cc_log
        if IS_WINDOWS and self.settings.value(
            "follow_control_center", False, type=bool
        ):
            self.cc_watch.start()
        # 记录 Win+A 热键兜底路径的工作状态（任务 4c）
        self._report_hotkey_status()

        # 连接成功后记住设备（品牌一起记，下次可直接路由）
        self.ctrl.connected.connect(self._on_connected)

        # v3.20：预热蓝牙枚举 DLL 与华为驱动（首次 import 要几百毫秒），
        # 否则「耳机一连上就弹窗」这条路径会卡在第一次导入上，显得很慢
        threading.Thread(target=self._warm_up, name="warm-up", daemon=True).start()

        # ---- 自动识别 / 自动连接（v3.17）----
        demo_requested = ("--demo" in argv
                          or any(a.startswith("--demo=") for a in argv))
        self.auto = AutoConnectManager(self.ctrl, self.settings)
        self.auto.set_log_callback(self._auto_log)
        self.auto.auto_connected.connect(self._on_auto_connected)
        self.auto.hint.connect(self.flyout.set_detect_hint)
        self.auto.snapshot.connect(self._on_auto_snapshot)
        self.flyout.auto_detect_callback = self.auto.set_enabled
        self.flyout.auto_show_callback = self.auto.set_auto_show
        self.tray.auto_detect_callback = self._set_auto_detect_from_tray
        if not demo_requested:
            self.auto.start()

        # 启动策略
        self._bootstrap(argv)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _warm_up() -> None:
        """后台预热：加载 bthprops.cpl + 华为 vendor 树（失败无所谓）。"""
        try:
            from pods import btdevices
            btdevices.list_bt_devices()
        except Exception:
            pass
        try:
            from huawei.controller import EarbudController  # noqa: F401
            from huawei.in_ear_patch import patched_models  # noqa: F401
        except Exception:
            pass

    def _install_hotkeys(self) -> None:
        try:
            hwnd = int(self.flyout.winId())
            self.hotkeys.install(hwnd)
        except Exception as exc:
            print(f"热键安装失败：{exc}", file=sys.stderr)

    def _report_hotkey_status(self) -> None:
        """把 Win+A / Ctrl 热键的工作状态写进控制中心日志（任务 4c）。"""
        try:
            st = self.hotkeys.status()
            detail = (f"hotkey_registered={st.get('hotkey_registered')}, "
                      f"keyboard_hook_installed={st.get('keyboard_hook_installed')}, "
                      f"follow_win_a={st.get('follow_win_a')}, "
                      f"last_error={st.get('last_error')}")
            err = st.get("error_detail")
            if err:
                detail += f"，安装错误：{err}"
            ok = bool(st.get("hotkey_registered")) and \
                bool(st.get("keyboard_hook_installed"))
            self.cc_watch.note_hotkey(ok, detail)
        except Exception as exc:
            try:
                self.cc_watch.note_hotkey(False, f"状态读取异常：{exc}")
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # 启动策略
    # ------------------------------------------------------------------ #
    def _bootstrap(self, argv: list[str]) -> None:
        demo_vendor = ""
        for arg in argv:
            if arg == "--demo":
                demo_vendor = VIVO
            elif arg.startswith("--demo="):
                value = arg.split("=", 1)[1].strip().lower()
                demo_vendor = HUAWEI if value in ("huawei", "hw", "honor") else VIVO

        if demo_vendor:
            self.ctrl.connect_demo(demo_vendor)
            self.flyout.show_flyout()
            return

        # 自动识别已连接的耳机（manager.start 里已立刻 poll 一次）；
        # 给它一点时间完成握手，再决定要不要回退到「上次设备」。
        QTimer.singleShot(900, self._bootstrap_fallback)

    def _bootstrap_fallback(self) -> None:
        if self.ctrl.info is not None:
            # 自动识别已经接管（正在连接或已连上），不再插一脚
            return
        last_mac = self.settings.value("last_device_mac", "")
        last_name = self.settings.value("last_device_name", "")
        last_vendor = self.settings.value("last_device_vendor", "", type=str)
        if last_mac and last_name:
            self.ctrl.connect_device(
                DeviceInfo(last_name, last_mac, vendor=last_vendor))
        else:
            # 首次启动：展示浮窗引导连接（面板里会显示自动识别提示）
            self.flyout.show_flyout()

    # ------------------------------------------------------------------ #
    # 自动识别回调
    # ------------------------------------------------------------------ #
    def _on_auto_connected(self, info) -> None:
        # 只做 UI 侧动作；「记住上次设备」放在真正连上之后（_on_connected），
        # 免得把一次失败的自动连接写成下次启动的默认设备
        self._auto_log(f"自动接管：{info.name}（{info.vendor or '未知品牌'}）")
        if self.auto.auto_show:
            self.flyout.show_flyout(source="auto")

    def _on_auto_snapshot(self, devices) -> None:
        """检测结果变化只写日志。

        刻意**不改**托盘 tooltip：那里的内容由电量回调维护（左/右/盒百分比），
        每 3s 覆盖一次会把电量提示刷掉。
        """
        if devices:
            self._auto_log("检测到已连接耳机：" +
                           "、".join(f"{d.name}({d.mac})" for d in devices))

    def _set_auto_detect_from_tray(self, enabled: bool) -> None:
        self.auto.set_enabled(enabled)
        try:
            self.tray.autodetect_act.setChecked(bool(enabled))
        except Exception:
            pass

    def _auto_log(self, text: str) -> None:
        """自动识别的过程写进日志，便于真机排查。

        两处都写：`ccwatch.log`（与控制中心跟随的日志放一起）与 `taskbar.log`
        （后者不受「记录控制中心检测日志」开关影响，始终有记录）。
        """
        try:
            self.cc_watch._log(f"[auto] {text}")
        except Exception:
            pass
        try:
            from ui.taskbar import log as _tb_log
            _tb_log(f"[auto] {text}")
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    def _on_connected(self, ok: bool) -> None:
        if ok and self.ctrl.device and not self.ctrl.device.is_demo:
            info = DeviceInfo(self.ctrl.device.name, self.ctrl.device.mac,
                              vendor=self.ctrl.vendor)
            self._remember(info)
            self.flyout.set_detect_hint("")

    def _remember(self, info) -> None:
        if not getattr(info, "mac", "") or getattr(info, "is_demo", False):
            return
        self.settings.setValue("last_device_mac", info.mac)
        self.settings.setValue("last_device_name", info.name)
        self.settings.setValue("last_device_vendor", getattr(info, "vendor", ""))

    # ------------------------------------------------------------------ #
    def _set_cc_watch(self, enabled: bool) -> None:
        """托盘 / 浮窗里切换「跟随系统控制中心」时启停检测器。"""
        if not IS_WINDOWS:
            return
        try:
            if enabled:
                self.cc_watch.start()
            else:
                self.cc_watch.stop()
        except Exception:
            pass

    def _set_cc_log(self, enabled: bool) -> None:
        """切换控制中心检测日志写入。"""
        try:
            self.cc_watch.log_enabled = bool(enabled)
            self.cc_watch._log(f"日志开关切换为：{bool(enabled)}")
        except Exception:
            pass

    def shutdown(self) -> None:
        try:
            self.auto.stop()
        except Exception:
            pass
        try:
            self.cc_watch.stop()
        except Exception:
            pass
        try:
            self.hotkeys.uninstall()
        except Exception:
            pass
        try:
            self.ctrl.disconnect()
        except Exception:
            pass
        self.qt.quit()

    def run(self) -> int:
        return self.qt.exec()


def main() -> None:
    app = VivoPodsApp(sys.argv)
    sys.exit(app.run())


if __name__ == "__main__":
    main()
