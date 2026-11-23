' Copy to start-bridge-silent.vbs and adjust paths if needed, then place in shell:startup for auto-start.
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
WshShell.Run "pythonw """ & WshShell.CurrentDirectory & "\bridge.py""", 0, False
