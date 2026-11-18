"""Register one macOS shortcut without an event tap or an input monitor."""
import ctypes as C
import logging

_CARBON = '/System/Library/Frameworks/Carbon.framework/Carbon'
_EVENT_CLASS_KEYBOARD = int.from_bytes(b'keyb', 'big')
_HOTKEY_PRESSED = 5
_VK_F1 = 0x7A


class _EventType(C.Structure):
    _fields_ = [('eventClass', C.c_uint32), ('eventKind', C.c_uint32)]


class _HotKeyID(C.Structure):
    _fields_ = [('signature', C.c_uint32), ('id', C.c_uint32)]


_Ref = C.c_void_p
_Handler = C.CFUNCTYPE(C.c_int32, _Ref, _Ref, _Ref)


class PauseHotkey:
    def __init__(self, on_press):
        carbon = C.CDLL(_CARBON)
        carbon.GetApplicationEventTarget.argtypes = ()
        carbon.GetApplicationEventTarget.restype = _Ref
        carbon.InstallEventHandler.argtypes = (_Ref, _Handler, C.c_uint32, C.POINTER(_EventType),
                                               _Ref, C.POINTER(_Ref))
        carbon.InstallEventHandler.restype = C.c_int32
        carbon.RegisterEventHotKey.argtypes = (C.c_uint32, C.c_uint32, _HotKeyID, _Ref,
                                               C.c_uint32, C.POINTER(_Ref))
        carbon.RegisterEventHotKey.restype = C.c_int32
        carbon.RemoveEventHandler.argtypes = (_Ref,)
        carbon.RemoveEventHandler.restype = C.c_int32
        carbon.UnregisterEventHotKey.argtypes = (_Ref,)
        carbon.UnregisterEventHotKey.restype = C.c_int32
        self._carbon = carbon
        self._event_handler = _Ref()
        self._registration = _Ref()

        @_Handler
        def handler(call, event, context):
            try:
                on_press()
            except Exception:
                logging.exception('Pause shortcut failed')
                return -9874  # eventNotHandledErr
            return 0

        self._callback = handler
        target = carbon.GetApplicationEventTarget()
        event = _EventType(_EVENT_CLASS_KEYBOARD, _HOTKEY_PRESSED)
        status = carbon.InstallEventHandler(target, handler, 1, C.byref(event), None,
                                             C.byref(self._event_handler))
        if status:
            raise OSError(f'暂停快捷键处理器安装失败（{status}）')
        identifier = _HotKeyID(int.from_bytes(b'DIBP', 'big'), 1)
        status = carbon.RegisterEventHotKey(_VK_F1, 0, identifier, target, 0,
                                            C.byref(self._registration))
        if status:
            self.close()
            raise OSError(f'F1 注册失败（{status}）')

    def close(self):
        if self._registration:
            self._carbon.UnregisterEventHotKey(self._registration)
            self._registration = _Ref()
        if self._event_handler:
            self._carbon.RemoveEventHandler(self._event_handler)
            self._event_handler = _Ref()
