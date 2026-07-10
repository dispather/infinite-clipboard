Set FSO = CreateObject("Scripting.FileSystemObject")
Set WshShell = CreateObject("WScript.Shell")

' 스크립트가 있는 폴더로 이동
scriptDir = FSO.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = scriptDir

' pip으로 의존성 설치 후 main.py 직접 실행 (콘솔 창 숨김)
WshShell.Run "cmd /c python -m pip install -q -r requirements_win.txt 2>nul & python main.py", 0, True
