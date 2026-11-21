"""Offline Settings panel, independent of Network Connection and any browser."""
from PySide2.QtCore import QObject,QProcess,Signal,Slot


class UsbModuleManager(QObject):
    module_usb_result=Signal(str,arguments=['result'])

    @Slot(str,str,str,str)
    def module_usb(self,action,selection,revision,confirmation):
        if getattr(self,'process',None) is not None:
            self.module_usb_result.emit('{"error":"Module Manager is busy."}');return
        process=QProcess(self);self.process=process
        def finish(*unused):
            if self.process is not process:return
            self.process=None
            result=bytes(process.readAllStandardOutput()).decode('utf-8',errors='replace').strip()
            process.deleteLater()
            self.module_usb_result.emit(result or '{"error":"Module Manager could not finish the request."}')
        process.finished.connect(finish)
        process.errorOccurred.connect(lambda error:finish() if error==QProcess.FailedToStart else None)
        process.start('/usr/bin/python3',['-I','-B','/opt/hector-storage-manager/app/module_usb.py','--',action,selection,revision,confirmation])
