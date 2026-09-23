from __future__ import annotations

import ctypes as ct
import json
import platform
import plistlib
from pathlib import Path
import subprocess
import sys
import time


APP_PATH = Path("/Applications/同花顺.app")
SUPPORTED_VERSION = ("5.3.5", "7.0.62")
MAX_NODES = 8000


class MacClient:
    def _helper(self, action, pid=None):
        from desktop.tonghuashun import BridgeError
        args = [sys.executable, str(Path(__file__).resolve()), action]
        if pid is not None:
            args.append(str(pid))
        try:
            result = subprocess.run(args, capture_output=True,
                                    timeout=12 if action == "snapshot" else 3, check=True)
            if len(result.stdout) > 2_000_000:
                raise ValueError("oversized")
            data = json.loads(result.stdout)
            if data.get("error"):
                raise BridgeError(data["error"])
            return data
        except BridgeError:
            raise
        except (subprocess.SubprocessError, OSError, ValueError):
            raise BridgeError("READ_FAILED") from None

    def inspect(self):
        from desktop.tonghuashun import BridgeError
        runtime_app = Path(sys.base_prefix) / "Resources/Python.app"
        result = {"installed": False, "running": False, "accessibility": False,
                  "version": None, "runtime": sys.executable,
                  "runtime_app": str(runtime_app) if runtime_app.is_dir() else None}
        if platform.system() != "Darwin":
            return {**result, "code": "UNSUPPORTED_PLATFORM"}
        try:
            with (APP_PATH / "Contents/Info.plist").open("rb") as stream:
                info = plistlib.load(stream)
        except (OSError, ValueError, plistlib.InvalidFileException):
            return {**result, "code": "NOT_INSTALLED"}
        if info.get("CFBundleIdentifier") != "cn.com.10jqka.macstock":
            return {**result, "code": "NOT_INSTALLED"}
        result.update(installed=True, version=info.get("CFBundleShortVersionString"))
        if (result["version"], info.get("CFBundleVersion")) != SUPPORTED_VERSION:
            return {**result, "code": "UNSUPPORTED_VERSION"}
        try:
            pids = client_pids()
        except BridgeError as exc:
            return {**result, "code": exc.code}
        if len(pids) > 1:
            return {**result, "code": "MULTIPLE_CLIENTS"}
        result["running"] = bool(pids)
        result["accessibility"] = bool(self._helper("trust").get("trusted"))
        code = ("NOT_RUNNING" if not pids else
                "READY_TO_READ" if result["accessibility"] else "ACCESSIBILITY_REQUIRED")
        return {**result, "code": code}

    def launch(self):
        from desktop.tonghuashun import BridgeError
        state = self.inspect()
        if not state["installed"]:
            raise BridgeError(state["code"])
        if state["code"] in ("MULTIPLE_CLIENTS", "CLIENT_LOCATION_REQUIRED"):
            raise BridgeError(state["code"])
        # 只启动固定安装路径，不接受 URL、股票代码或任意系统命令。
        try:
            subprocess.run(["/usr/bin/open", "-a", str(APP_PATH)], timeout=8, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (subprocess.SubprocessError, OSError):
            raise BridgeError("READ_FAILED") from None

    def request_permission(self):
        from desktop.tonghuashun import BridgeError
        if platform.system() != "Darwin":
            raise BridgeError("UNSUPPORTED_PLATFORM")
        return self._helper("permission")["trusted"]

    def read(self):
        from desktop.tonghuashun import BridgeError
        pids = client_pids()
        if len(pids) != 1:
            raise BridgeError("MULTIPLE_CLIENTS" if pids else "NOT_RUNNING")
        return self._helper("snapshot", pids[0])["tree"]


def client_pids():
    from desktop.tonghuashun import BridgeError
    output = subprocess.run(["/bin/ps", "-A", "-o", "pid=,comm="], capture_output=True,
                            text=True, timeout=2, check=True).stdout
    matches = []
    for line in output.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2 and parts[1].endswith("/同花顺.app/Contents/MacOS/同花顺"):
            if parts[1] != str(APP_PATH / "Contents/MacOS/同花顺"):
                raise BridgeError("CLIENT_LOCATION_REQUIRED")
            matches.append(int(parts[0]))
    return matches


class AccessibilityReader:
    """只读取辅助功能属性，不包含点击、输入或设置属性的能力。"""

    def __init__(self):
        self.cf = ct.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        self.ax = ct.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        signatures = {
            "CFRelease": (None, [ct.c_void_p]),
            "CFGetTypeID": (ct.c_ulong, [ct.c_void_p]),
            "CFStringGetTypeID": (ct.c_ulong, []),
            "CFStringCreateWithCString": (ct.c_void_p, [ct.c_void_p, ct.c_char_p, ct.c_uint32]),
            "CFStringGetCString": (ct.c_bool, [ct.c_void_p, ct.c_char_p, ct.c_long, ct.c_uint32]),
            "CFArrayGetCount": (ct.c_long, [ct.c_void_p]),
            "CFArrayGetValueAtIndex": (ct.c_void_p, [ct.c_void_p, ct.c_long]),
            "CFArrayGetTypeID": (ct.c_ulong, []),
            "CFBooleanGetValue": (ct.c_bool, [ct.c_void_p]),
            "CFBooleanGetTypeID": (ct.c_ulong, []),
            "CFDictionaryCreate": (ct.c_void_p, [ct.c_void_p, ct.POINTER(ct.c_void_p),
                                                ct.POINTER(ct.c_void_p), ct.c_long,
                                                ct.c_void_p, ct.c_void_p]),
        }
        for name, (restype, argtypes) in signatures.items():
            function = getattr(self.cf, name)
            function.restype, function.argtypes = restype, argtypes
        self.ax.AXIsProcessTrusted.restype = ct.c_bool
        self.ax.AXIsProcessTrusted.argtypes = []
        self.ax.AXIsProcessTrustedWithOptions.restype = ct.c_bool
        self.ax.AXIsProcessTrustedWithOptions.argtypes = [ct.c_void_p]
        self.ax.AXUIElementCreateApplication.restype = ct.c_void_p
        self.ax.AXUIElementCreateApplication.argtypes = [ct.c_int]
        self.ax.AXUIElementCopyAttributeValue.restype = ct.c_int
        self.ax.AXUIElementCopyAttributeValue.argtypes = [ct.c_void_p, ct.c_void_p, ct.POINTER(ct.c_void_p)]
        self.ax.AXUIElementSetMessagingTimeout.restype = ct.c_int
        self.ax.AXUIElementSetMessagingTimeout.argtypes = [ct.c_void_p, ct.c_float]
        self.started = time.monotonic()
        self.count = 0

    def trusted(self, prompt=False):
        if not prompt:
            return bool(self.ax.AXIsProcessTrusted())
        key = ct.c_void_p.in_dll(self.ax, "kAXTrustedCheckOptionPrompt")
        value = ct.c_void_p.in_dll(self.cf, "kCFBooleanTrue")
        options = self.cf.CFDictionaryCreate(None, (ct.c_void_p * 1)(key.value),
                                            (ct.c_void_p * 1)(value.value), 1, None, None)
        try:
            return bool(self.ax.AXIsProcessTrustedWithOptions(options))
        finally:
            self.cf.CFRelease(options)

    def attribute(self, element, name):
        key = self.cf.CFStringCreateWithCString(None, name.encode(), 0x08000100)
        result = ct.c_void_p()
        try:
            error = self.ax.AXUIElementCopyAttributeValue(element, key, ct.byref(result))
            if error in (-25205, -25212):
                return None
            if error != 0:
                raise RuntimeError("attribute unavailable")
            return result.value
        finally:
            self.cf.CFRelease(key)

    def text(self, element, name):
        value = self.attribute(element, name)
        if not value:
            return ""
        try:
            if self.cf.CFGetTypeID(value) != self.cf.CFStringGetTypeID():
                return ""
            buffer = ct.create_string_buffer(4096)
            if not self.cf.CFStringGetCString(value, buffer, len(buffer), 0x08000100):
                raise RuntimeError("text too large")
            return buffer.value.decode("utf-8")
        finally:
            self.cf.CFRelease(value)

    def children(self, element, attribute, depth):
        value = self.attribute(element, attribute)
        if not value:
            return []
        try:
            if self.cf.CFGetTypeID(value) != self.cf.CFArrayGetTypeID():
                raise RuntimeError("invalid children")
            result = []
            for index in range(self.cf.CFArrayGetCount(value)):
                child = self.walk(self.cf.CFArrayGetValueAtIndex(value, index), depth)
                if child is not None:
                    result.append(child)
            return result
        finally:
            self.cf.CFRelease(value)

    def walk(self, element, depth=0):
        self.count += 1
        if self.count > MAX_NODES or depth > 32 or time.monotonic() - self.started > 10:
            raise RuntimeError("incomplete tree")
        role = self.text(element, "AXRole")
        # 不读取输入框、密码框、工具栏、资讯和列重复视图，避免收集凭据及无关内容。
        if role in {"AXColumn", "AXToolbar", "AXMenuBar", "AXImage", "AXTextArea"}:
            return None
        if role in {"AXTextField", "AXSecureTextField", "AXSheet"}:
            return {"role": role, "text": "", "children": []}
        text = ""
        if role in {"AXStaticText", "AXButton", "AXCheckBox", "AXPopUpButton", "AXUnknown", "text"}:
            text = self.text(element, "AXValue") or self.text(element, "AXTitle")
        result = {"role": role, "text": text, "children": self.children(element, "AXChildren", depth + 1)}
        if role == "AXWindow":
            result["subrole"] = self.text(element, "AXSubrole")
        return result

    def snapshot(self, pid):
        if not self.trusted():
            return {"error": "ACCESSIBILITY_REQUIRED"}
        if client_pids() != [pid]:
            return {"error": "MULTIPLE_CLIENTS"}
        app = self.ax.AXUIElementCreateApplication(pid)
        try:
            self.ax.AXUIElementSetMessagingTimeout(app, 0.5)
            windows = self.children(app, "AXWindows", 0)
            if not windows:
                return {"error": "READ_FAILED"}
            return {"tree": {"role": "AXApplication", "text": "", "children": windows}}
        finally:
            self.cf.CFRelease(app)


def main():
    try:
        reader = AccessibilityReader()
        action = sys.argv[1]
        if action in ("trust", "permission"):
            result = {"trusted": reader.trusted(prompt=action == "permission")}
        elif action == "snapshot":
            result = reader.snapshot(int(sys.argv[2]))
        else:
            result = {"error": "ACTION_UNAVAILABLE"}
    except Exception:
        result = {"error": "READ_FAILED"}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
