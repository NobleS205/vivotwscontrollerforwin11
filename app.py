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

# ⚠️ 看门狗进程入口必须放在**所有 PySide6 导入之前**。
# 它只做「等主进程结束 → 必要时重新拉起」，常驻数小时，不能白白把 Qt
# 拖进内存；走这条分支时也不会创建窗口、不会出现在任务栏。
if "--watchdog" in sys.argv:
    from ui.keepalive import watchdog_main
    raise SystemExit(watchdog_main(sys.argv))

import os
import threading
import time

from PySide6.QtCore import QSettings, QTimer, Qt
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from appmeta import APP_NAME, APP_VERSION
from pods.autoconnect import AutoConnectManager
from pods.detect import HUAWEI, VIVO, is_supported_device
from pods.hub import DeviceInfo, PodsHub
from ui import keepalive
from ui.ccwatch import ControlCenterWatcher
from ui.flyout import FlyoutWindow
from ui.hotkey import HotkeyManager, IS_WINDOWS
from ui.power import SessionWatcher
from ui.tray import TrayController, build_app_icon


# 注意：QSettings 的组织/应用名保持 "VivoPodsCC" 不变 —— 它决定配置文件路径，
# 改了会丢掉用户的「上次设备 / 深色模式 / 自动识别」等已有设置。
SETTINGS_ORG = "VivoPodsCC"
SETTINGS_APP = "config"


class VivoPodsApp:
    def __init__(self, argv: list[str]) -> None:
        keepalive.set_started_at(time.time())
        self._already_running = False
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
        self.qt = QApplication(argv)
        self.qt.setApplicationName("VivoPods Controller")
        self.qt.setQuitOnLastWindowClosed(False)
        self.qt.aboutToQuit.connect(self.shutdown)

        # ---- 单实例（v3.28）----
        # 重复双击时不再起第二个托盘 + 第二个看门狗，只把已有窗口唤出来。
        # 注意顺序：必须在 install_crash_guard / spawn_watchdog 之前，
        # 否则第二个进程退出时会写下 clean_exit 标记，把第一实例的
        # 「崩溃 → 自动重启」判断污染成「用户主动退出」。
        self.instance_server = keepalive.acquire_single_instance()
        if self.instance_server is None:
            self._already_running = True
            return

        # ---- 常驻保护（v3.28）----
        # 原生崩溃写 crash.log；Python 异常只记录不退出；进程被外部结束后
        # 由看门狗按「有无崩溃证据」决定是否重新拉起（手动杀 = 不拉起）。
        keepalive.install_crash_guard()
        keepalive.log_runtime(f"启动 pid={os.getpid()} "
                              f"version={APP_VERSION} argv={' '.join(argv[1:])}")

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

        # ---- 睡眠 / 锁屏监控（v3.34）----
        # 电脑一直开着但夜里睡过一觉，早上回来「后台挂载没了」：进程其实还在，
        # 是托盘图标被 Windows 重建掉了 + 蓝牙链路断了但控制器还记着「已连接」。
        # 这里只负责在正确的时机发信号，具体动作在 `_after_resume` 里做。
        self._last_resume = 0.0
        self.session = SessionWatcher()
        self.session.woke.connect(self._on_system_woke)
        self.session.unlocked.connect(self._on_session_unlocked)
        if IS_WINDOWS:
            self.session.start()

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

        # v3.33：打包版首次运行（或换了目录）时补一个桌面快捷方式。
        # 延后 1.2s 再做：要起一次 PowerShell，别占着首屏。
        QTimer.singleShot(1200, self._ensure_desktop_shortcut)

        # 第二个实例发来的 "show" → 唤出已有面板
        keepalive.install_instance_activator(
            self.instance_server, self.flyout.show_flyout)

        # 心跳：每 10 分钟一行（含 RSS 内存）。挂机后进程消失时，
        # 靠「最后一跳的时间」与「有无退出记录」区分崩溃 / 被外部结束。
        self._hb_timer = QTimer(self.qt)
        self._hb_timer.setInterval(10 * 60 * 1000)
        self._hb_timer.timeout.connect(keepalive.log_heartbeat)
        self._hb_timer.start()
        keepalive.log_heartbeat()

        # 看门狗放到初始化最后：早期失败不该被当成「崩溃」反复拉起
        keepalive.spawn_watchdog()

        if "--restarted" in argv:
            QTimer.singleShot(2500, self._notify_restarted)

    # ------------------------------------------------------------------ #
    # 桌面快捷方式（v3.33）
    # ------------------------------------------------------------------ #
    def _ensure_desktop_shortcut(self) -> None:
        """在桌面放一个指向 exe 的快捷方式，解压后双击图标即可运行。

        只在打包版生效（源码运行会把快捷方式指到 python.exe）。配置里记着
        当前 exe、快捷方式也还在，就连 PowerShell 都不起；用户手动删掉后
        不再反复生成，换了目录则重新指向新位置。
        """
        try:
            from ui import shortcut

            exe = shortcut.exe_path()
            if not exe:
                return
            prev = self.settings.value("shortcut_target", "", type=str)
            if (prev
                    and os.path.normcase(prev) == os.path.normcase(exe)
                    and os.path.exists(shortcut.lnk_path())):
                return
            got = shortcut.ensure_desktop_shortcut(prev)
            if got:
                self.settings.setValue("shortcut_target", got)
                keepalive.log_runtime(f"桌面快捷方式就绪：{shortcut.lnk_path()}")
        except Exception as exc:
            keepalive.log_runtime(f"桌面快捷方式创建失败：{exc}")

    # ------------------------------------------------------------------ #
    def _notify_restarted(self) -> None:
        """被看门狗重新拉起时提示一次，避免用户以为托盘图标是自己冒出来的。"""
        try:
            self.tray.showMessage(
                APP_NAME,
                "程序刚才异常退出，已自动恢复运行。"
                "日志：%LOCALAPPDATA%\\VivoPodsCC\\crash.log",
                QSystemTrayIcon.Information, 8000)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # 睡眠唤醒 / 解锁（v3.34）
    # ------------------------------------------------------------------ #
    def _on_system_woke(self, slept: float) -> None:
        keepalive.log_runtime(f"检测到系统睡眠唤醒（约 {slept / 60:.0f} 分钟）")
        # 睡眠时蓝牙必然断开，必须重置链路
        self._after_resume("睡眠唤醒", reset_link=True)

    def _on_session_unlocked(self) -> None:
        keepalive.log_runtime("检测到会话解锁")
        # 只是锁屏（没睡）时蓝牙通常还连着，只重挂托盘，不做无谓的断开重连
        self._after_resume("解锁", reset_link=False)

    def _after_resume(self, reason: str, reset_link: bool = True) -> None:
        """睡眠 / 锁屏之后把常驻状态重新拉起来。

        三件事，各自独立兜异常：
          1. 重挂托盘图标（Windows 唤醒后通知区域会重建，旧图标就"消失"了）；
          2. 让自动识别丢掉旧快照，按当前真实蓝牙状态重新判断；
          3. 睡眠唤醒时当前设备若还记着「已连接」，先断开再重连 —— 睡眠时
             蓝牙必断，控制器状态却是旧的，不重置就不会自己恢复。
        两个信号可能同时到（唤醒通常伴随解锁），8 秒内只执行一次。
        """
        now = time.monotonic()
        if now - self._last_resume < 8.0:
            return
        self._last_resume = now
        keepalive.log_runtime(f"{reason}：开始恢复后台挂载")

        try:
            self.tray.remount(reason)
        except Exception as exc:
            keepalive.log_runtime(f"{reason}：重挂托盘失败：{exc}")

        try:
            self.auto.notify_resume()
        except Exception as exc:
            keepalive.log_runtime(f"{reason}：重置自动识别失败：{exc}")

        info = self.ctrl.info
        if info is None or getattr(info, "is_demo", False):
            return
        if not is_supported_device(info.name):
            return
        if not reset_link:
            return
        keepalive.log_runtime(f"{reason}：重置链路并重连 {info.name}（{info.mac}）")
        try:
            self.ctrl.disconnect()
        except Exception:
            pass
        QTimer.singleShot(1200, lambda i=info: self._reconnect_after_resume(i))

    def _reconnect_after_resume(self, info) -> None:
        # 这 1.2 秒里用户可能已经手动选了别的设备，不要把它顶掉
        cur = self.ctrl.info
        if cur is not None and (cur.mac or "").upper() != (info.mac or "").upper():
            return
        try:
            self.ctrl.connect_device(info)
        except Exception as exc:
            keepalive.log_runtime(f"唤醒重连失败：{exc}")

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
            # 打包成 console=False 时 sys.stderr 是 None，print 会再抛一次
            keepalive.log_runtime(f"热键安装失败：{exc}")

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
        if last_mac and last_name and is_supported_device(last_name):
            self.ctrl.connect_device(
                DeviceInfo(last_name, last_mac, vendor=last_vendor))
        else:
            # 首次启动 / 记忆里的设备已不在支持列表：展示浮窗引导连接
            self.flyout.show_flyout()

    # ------------------------------------------------------------------ #
    # 自动识别回调
    # ------------------------------------------------------------------ #
    def _on_auto_connected(self, info) -> None:
        # 只做 UI 侧动作；「记住上次设备」放在真正连上之后（_on_connected），
        # 免得把一次失败的自动连接写成下次启动的默认设备
        #
        # v3.34：再验一次型号。自动识别已经过滤过，这里兜住将来别的调用方
        # 直接发信号的情况 —— 名单外的设备（HUAWEI 手机 / 笔记本、未适配的
        # FreeBuds 7）绝不弹面板。
        if not is_supported_device(getattr(info, "name", "")):
            self._auto_log(f"忽略未支持设备：{getattr(info, 'name', '?')}（不弹窗）")
            return
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
            # v3.29：把「识别结果 + 能力矩阵」落盘。以后遇到「某个型号少了
            # 某个分区」，先看这一行就知道是名字没认出来、还是矩阵本来就没
            # 声明，不用再猜。
            try:
                fs = self.ctrl.features
                keepalive.log_runtime(
                    f"连接 {info.name}（{info.mac}）vendor={info.vendor or '未知'} "
                    f"model={self._model_label(info)} "
                    f"features={sorted(fs.supported)} forced={sorted(fs.forced)}")
            except Exception:
                pass

    @staticmethod
    def _model_label(info) -> str:
        """能力矩阵认出来的型号名（认不出时退回「家族兼容」）。"""
        try:
            if getattr(info, "vendor", "") == HUAWEI:
                from huawei.huawei_models import display_name
            else:
                from vivo.vivo_models import display_name
            return display_name(info.name)
        except Exception:
            return "?"

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
        # 先落「主动退出」标记：看门狗读到它就不会把这次退出当成崩溃重启
        keepalive.mark_clean_exit()
        try:
            self.auto.stop()
        except Exception:
            pass
        try:
            self.session.stop()
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
        if self._already_running:
            # 已有实例：请求已发出，本进程什么都不做直接退出
            return 0
        return self.qt.exec()


def _ensure_stdio() -> None:
    """`console=False` 打包时 sys.stdout / sys.stderr 是 None。

    此时任何 `print()` 都会抛 AttributeError；如果它恰好发生在 Qt 槽函数里，
    就变成一次「随机崩溃」。统一接到 devnull，彻底堵掉这个雷。
    """
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            try:
                setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
            except Exception:
                pass


def main() -> None:
    _ensure_stdio()
    app = VivoPodsApp(sys.argv)
    sys.exit(app.run())


if __name__ == "__main__":
    main()
