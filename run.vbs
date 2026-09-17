Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' Get directory where this script is located
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
pyScript = fso.BuildPath(scriptDir, "antigravity_tray.py")

' Run pythonw.exe with no console window
WshShell.Run "pythonw """ & pyScript & """", 0, False
